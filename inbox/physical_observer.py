#!/usr/bin/env python3
"""Causal passive QS/HF forward observer with held-out prediction.
Model: C0 || G || (R1 series C1), tau=R1*C1.
Input: measured DUT voltage, piecewise linear between knots. No SCPI.
"""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

P_NAMES=['C0_F','G_S','C1_F','tau_s']

def trajectory(knots,times,p):
    C0,G,C1,tau=p
    kt=knots.t_s.to_numpy(float); kv=knots.v_V.to_numpy(float)
    tt=np.unique(np.r_[kt,np.asarray(times,float)])
    if tt[0]<kt[0]-1e-12 or tt[-1]>kt[-1]+1e-12: raise ValueError('window outside voltage history')
    vv=np.interp(tt,kt,kv); xx=np.empty(len(tt)); xx[0]=float(knots.x0_V.iloc[0])
    source=0.; loss=0.; qport=0.; energy0=.5*C0*vv[0]**2+.5*C1*xx[0]**2
    for j,d in enumerate(np.diff(tt)):
        v=vv[j]; slope=(vv[j+1]-v)/d; x=xx[j]
        z=d/tau; e=np.exp(-z); one=-np.expm1(-z)
        # Exact propagation for a linear voltage segment.
        A=(v-x)/tau-slope
        xx[j+1]=x+slope*d+A*tau*one
        v_integral=v*d+.5*slope*d*d
        v2_integral=v*v*d+v*slope*d*d+slope*slope*d**3/3
        # Stable integral of t*exp(-t/tau).
        moment=tau*tau*(one-z*e) if z>1e-3 else tau*tau*(z*z/2-z**3/3+z**4/8-z**5/30+z**6/144)
        branch_work=C1*(slope*v_integral+A*(v*tau*one+slope*moment))
        branch_loss=C1*tau*(slope*slope*d+2*slope*A*tau*one+A*A*tau*(-np.expm1(-2*z))/2)
        source+=.5*C0*(vv[j+1]**2-v*v)+G*v2_integral+branch_work
        loss+=G*v2_integral+branch_loss
        qport+=C0*(vv[j+1]-v)+G*v_integral+C1*(xx[j+1]-x)
    energy1=.5*C0*vv[-1]**2+.5*C1*xx[-1]**2
    return tt,vv,xx,dict(source_work_J=source,dissipated_J=loss,stored_change_J=energy1-energy0,
        energy_residual_J=source-loss-(energy1-energy0),terminal_charge_C=qport)

def predict_qs(knots,rows,p):
    C0,G,C1,tau=p
    t,v,x,diag=trajectory(knots,np.r_[rows.a_s,rows.b_s],p)
    a=rows.a_s.to_numpy(); b=rows.b_s.to_numpy()
    va=np.interp(a,t,v); vb=np.interp(b,t,v)
    xa=np.interp(a,t,x); xb=np.interp(b,t,x)
    # Exact integral of piecewise-linear measured voltage.
    cumulative=np.r_[0,np.cumsum((v[:-1]+v[1:])/2*np.diff(t))]
    intv=np.interp(b,t,cumulative)-np.interp(a,t,cumulative)
    charge=C0*(vb-va)+C1*(xb-xa)+G*intv
    return charge/(b-a),diag

def predict_hf(rows,p):
    C0,G,C1,tau=p; w=2*np.pi*rows.frequency_Hz.to_numpy()
    return G+1j*w*C0+1j*w*C1/(1+1j*w*tau)

def load(folder):
    k=pd.read_csv(folder/'voltage.csv'); q=pd.read_csv(folder/'qs.csv')
    h=pd.read_csv(folder/'hf.csv') if (folder/'hf.csv').exists() else pd.DataFrame()
    for name,d,req in [('voltage',k,['run','split','t_s','v_V','x0_V']),('qs',q,['run','a_s','b_s','i_A','u_i_A'])]:
        if not set(req)<=set(d): raise ValueError(name+': missing columns')
        if not np.isfinite(d[[c for c in req if c not in ['run','split']]].to_numpy(float)).all(): raise ValueError('nonfinite '+name)
    if not k.split.isin(['train','validation']).all(): raise ValueError('invalid split')
    if (q.b_s<=q.a_s).any() or (q.u_i_A<=0).any(): raise ValueError('invalid QS window/uncertainty')
    histories={}; split={}
    for run,g in k.groupby('run',sort=False):
        g=g.sort_values('t_s')
        if len(g)<2 or (np.diff(g.t_s)<=0).any() or g.x0_V.nunique()!=1 or g.split.nunique()!=1: raise ValueError('invalid history')
        histories[run]=g; split[run]=g.split.iloc[0]
    if not set(q.run)<=set(histories): raise ValueError('QS history missing')
    if len(h):
        req=['run','frequency_Hz','g_S','b_S','u_g_S','u_b_S']
        if not set(req)<=set(h) or not set(h.run)<=set(histories): raise ValueError('invalid HF input')
        if not np.isfinite(h[req[1:]].to_numpy(float)).all() or (h[['frequency_Hz','u_g_S','u_b_S']]<=0).any().any(): raise ValueError('invalid HF values')
    return histories,split,q,h

def predict(histories,q,h,p):
    qp=q.copy(); qp['predicted_A']=np.nan; balances=[]
    for run,g in qp.groupby('run',sort=False):
        pred,diag=predict_qs(histories[run],g,p)
        qp.loc[g.index,'predicted_A']=pred
        balances.append(dict(run=run,**diag))
    hp=h.copy()
    if len(h):
        y=predict_hf(h,p); hp['predicted_g_S']=y.real; hp['predicted_b_S']=y.imag
    return qp,hp,balances

def residual(histories,q,h,p):
    qq,hh,_=predict(histories,q,h,p)
    parts=[((qq.predicted_A-qq.i_A)/qq.u_i_A).to_numpy()]
    if len(hh): parts += [((hh.predicted_g_S-hh.g_S)/hh.u_g_S).to_numpy(),((hh.predicted_b_S-hh.b_S)/hh.u_b_S).to_numpy()]
    return np.concatenate(parts)

def demo(folder):
    rng=np.random.default_rng(13); true=np.array([1e-9,1e-10,3e-10,.012])
    histories=[]; qs=[]; hs=[]
    for run,split,levels in [('cal','train',[0,.08,-.04,.06,0]),('future','validation',[0,.035,.09,-.025,0])]:
        times=[0.]; volts=[levels[0]]; cursor=.03
        for v in levels[1:]:
            times += [cursor,cursor+.004,cursor+.065]; volts += [volts[-1],v,v]; cursor+=.1
        knots=pd.DataFrame(dict(run=run,split=split,t_s=times,v_V=volts,x0_V=0.))
        histories.append(knots)
        a=np.arange(0,times[-1]-.002,.002); b=a+.0016
        r=pd.DataFrame(dict(run=run,a_s=a,b_s=b,i_A=0.,u_i_A=2e-12))
        val,_=predict_qs(knots,r,true); r['i_A']=val+rng.normal(0,2e-12,len(r)); qs.append(r)
        rr=pd.DataFrame(dict(run=run,frequency_Hz=[10.,100.,1000.,1e6],g_S=0.,b_S=0.,u_g_S=2e-11,u_b_S=2e-11))
        y=predict_hf(rr,true); rr['g_S']=y.real+rng.normal(0,2e-11,len(rr)); rr['b_S']=y.imag+rng.normal(0,2e-11,len(rr)); hs.append(rr)
    pd.concat(histories).to_csv(folder/'voltage.csv',index=False)
    pd.concat(qs).to_csv(folder/'qs.csv',index=False)
    pd.concat(hs).to_csv(folder/'hf.csv',index=False)

def metrics(histories,split,q,h,p):
    out={}
    for part in ['train','validation']:
        qq=q[q.run.map(split)==part]; hh=h[h.run.map(split)==part] if len(h) else h
        if len(qq):
            r=residual(histories,qq,hh,p)
            out[part]=dict(n_scalar_observations=len(r),normalized_RMSE=float(np.sqrt(np.mean(r*r))),
                fraction_abs_standardized_residual_le_2=float(np.mean(abs(r)<=2)))
    return out

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--data',type=Path,default=Path('observer_data')); a.add_argument('--out',type=Path,default=Path('observer_results'))
    a.add_argument('--demo',action='store_true'); a.add_argument('--parameters',type=Path,help='Frozen parameter JSON; skips fitting')
    x=a.parse_args(); x.out.mkdir(parents=True,exist_ok=True)
    if x.demo: x.data.mkdir(parents=True,exist_ok=True); demo(x.data)
    histories,split,q,h=load(x.data)
    training=q[q.run.map(split)=='train']; htrain=h[h.run.map(split)=='train'] if len(h) else h
    diagnostic={}
    if x.parameters:
        params=json.loads(x.parameters.read_text()); p=np.array([params[n] for n in P_NAMES])
        if not np.isfinite(p).all() or (p<=0).any(): raise ValueError('positive finite parameters required')
    else:
        if training.empty: raise ValueError('no training observations')
        low=np.log([1e-13,1e-15,1e-13,1e-5]); high=np.log([1e-5,1e-3,1e-5,10.])
        f=least_squares(lambda lp:residual(histories,training,htrain,np.exp(lp)),np.log([1e-9,1e-10,2e-10,.01]),bounds=(low,high),max_nfev=600)
        p=np.exp(f.x); singular=np.linalg.svd(f.jac,compute_uv=False)
        diagnostic=dict(fit_success=bool(f.success),jacobian_singular_values=singular.tolist(),
                        jacobian_condition=float(singular[0]/singular[-1]) if singular[-1] else None)
    params=dict(zip(P_NAMES,map(float,p))); (x.out/'parameters.json').write_text(json.dumps(params,indent=2))
    qp,hp,balances=predict(histories,q,h,p)
    qp['split']=qp.run.map(split); qp.to_csv(x.out/'qs_predictions.csv',index=False)
    if len(hp): hp['split']=hp.run.map(split); hp.to_csv(x.out/'hf_predictions.csv',index=False)
    pd.DataFrame(balances).to_csv(x.out/'balance_checks.csv',index=False)
    report=dict(model='C0 parallel G parallel series(R1,C1), R1=tau/C1',parameters=params,
        R1_ohm=float(p[3]/p[2]),diagnostics=diagnostic,metrics=metrics(histories,split,q,h,p),
        validation='Entire validation runs excluded from fitting; no output-driven state reset. Initial states supplied by preparation.',
        limitations='Positive parameters enforce passive constitutive model, not proof of trap identity or full observability. Rectangular current apertures, measured DUT voltage and stationary linear HF operator assumed. No HF modulation feedback on slow state in this model. Energy check is internal consistency, not independent calorimetry.')
    (x.out/'report.json').write_text(json.dumps(report,indent=2))
    print(x.out.resolve())
if __name__=='__main__': main()
