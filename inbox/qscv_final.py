#!/usr/bin/env python3
"""QSCV + 1 MHz E7-12 external-bias analysis, offline.

This is a data-analysis and validation tool, not an instrument driver.
It does not send SCPI, enable outputs, or assume unverified timing.

Inputs:
  acquisition.csv : aperture records from the B2910BL QS channel
  timing.csv      : common-epoch timing and plateau metadata
  hf.csv          : E7-12 1 MHz admittance records

QS charge model:
  Q_obs = sum_j (mean_current_j - mean_leakage_j) * aperture_duration_j
  C_sec = Q_corrected / delta_V

HF model:
  Y = G + jB, Cp = B / (2*pi*f)

E7-12: 20 kohm belongs to the DC feed; no AC divider assumed.
Optional QS columns vsource_before_V, vsource_after_V, ibias_before_A,
ibias_after_A allow DC endpoint reconstruction if --derive-dut-voltage.
Voltages v_before_V/v_after_V must otherwise already be actual DUT voltages.

Run:
  python qscv_final.py --demo --model exp2 --gap-mode model --output demo
  python qscv_final.py --acq acquisition.csv --timing timing.csv --hf hf.csv \
      --r-bias-ohm 20000 --output result
"""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

REQ_QS=['run','direction','step','v_before_V','v_after_V','a_s','b_s','i_A',
        'u_i_A','leak_A','u_leak_ind_A','u_dt_s','pre_settled']
KEY=['run','direction','step']
REQ_TM=KEY+['qs_start_s','qs_end_s','plateau_start_s',
            'plateau_end_s','v_plateau_V','ac_state']
REQ_HF=KEY+['hf_start_s','hf_end_s','frequency_Hz','g_S','b_S',
            'vac_rms_DUT_V','vac_rms_source_V','vdc_DUT_V']

def read_csv(path,required):
    d=pd.read_csv(path)
    missing=set(required)-set(d)
    if missing: raise ValueError(f'{path}: missing columns {sorted(missing)}')
    return d

def demo(folder):
    keys=[['demo','forward',k] for k in range(6)]
    qs=[]
    rng=np.random.default_rng(31)
    for k in range(6):
        v0=k*0.02; v1=v0+0.02
        a=np.arange(0,0.1,0.001); b=a+0.0008
        dv=v1-v0
        y=2e-12+(dv*1e-9/0.003)*mean_exp(a,b,0.003)+(dv*0.2e-9/0.02)*mean_exp(a,b,0.02)
        for j in range(len(a)):
            qs.append(['demo','forward',k,v0,v1,a[j],b[j],y[j]+rng.normal(0,2e-13),
                       2e-13,2e-12,2e-14,1e-7,k!=0])
    qs=pd.DataFrame(qs,columns=REQ_QS)
    tm=pd.DataFrame(keys,columns=KEY)
    tm['qs_start_s']=np.arange(6,dtype=float)
    tm['qs_end_s']=np.arange(6,dtype=float)+0.8
    tm['plateau_start_s']=np.arange(6,dtype=float)+0.1
    tm['plateau_end_s']=np.arange(6,dtype=float)+0.8
    tm['v_plateau_V']=(np.arange(6)+1)*0.02
    tm['ac_state']='on'
    hf=[]
    for k in range(6):
        for j in range(3):
            hf.append(['demo','forward',k,k+0.2+j*0.15,k+0.3+j*0.15,1e6,1e-6,
                       2*np.pi*1e6*0.8e-9,0.01,0.01,(k+1)*0.02])
    hf=pd.DataFrame(hf,columns=REQ_HF)
    qs.to_csv(folder/'demo_acquisition.csv',index=False)
    tm.to_csv(folder/'demo_timing.csv',index=False)
    hf.to_csv(folder/'demo_hf.csv',index=False)
    return folder/'demo_acquisition.csv',folder/'demo_timing.csv',folder/'demo_hf.csv'

def mean_exp(a,b,tau):
    return tau*np.exp(-a/tau)*(-np.expm1(-(b-a)/tau))/(b-a)

def exp_integral(p,l,r,n):
    val=p[0]*(r-l)
    for m in range(n):
        tau=np.exp(p[2+2*m])
        val+=p[1+2*m]*tau*(np.exp(-l/tau)-np.exp(-r/tau))
    return val

def exp_pred(p,a,b,n):
    y=np.full_like(a,p[0])
    for m in range(n):
        y+=p[1+2*m]*mean_exp(a,b,np.exp(p[2+2*m]))
    return y

def fit_exponential(a,b,y,u,n):
    scale=max(float(np.max(np.abs(y))),float(np.max(u)),1e-30)
    lo=max(float(np.min(b-a))/100,1e-9)
    hi=max(float(b[-1])*100,lo*1000)
    p=[float(y[-1]/scale)]
    for tau in np.geomspace(max(lo*10,b[-1]/100),max(lo*20,b[-1]/2),n):
        p += [float((y[0]-y[-1])/scale/n),float(np.log(tau))]
    lower=[-np.inf]+sum(([-np.inf,np.log(lo)] for _ in range(n)),[])
    upper=[np.inf]+sum(([np.inf,np.log(hi)] for _ in range(n)),[])
    fit=least_squares(lambda p:(exp_pred(p,a,b,n)-y/scale)/(u/scale),p,
                      bounds=(lower,upper),max_nfev=10000)
    dof=len(y)-len(p)
    red=float(np.sum(fit.fun**2)/dof)
    cov=np.linalg.pinv(fit.jac.T@fit.jac)*max(1,red)
    transform=np.ones(len(p)); transform[0]=scale
    for m in range(n): transform[1+2*m]=scale
    physical=fit.x*transform
    cov=cov*transform[:,None]*transform[None,:]
    return physical,cov,red,float(np.linalg.cond(fit.jac)),bool(fit.success)

def numerical_uncertainty(fun,p,cov):
    grad=[]
    for j in range(len(p)):
        h=max(abs(p[j])*1e-5,1e-18 if j==0 or j%2 else 1e-6)
        v=np.zeros(len(p)); v[j]=h
        grad.append((fun(p+v)-fun(p-v))/(2*h))
    g=np.array(grad)
    return float(np.sqrt(max(0,g@cov@g)))

def analyse_qs(df,args):
    rows=[]; details=[]
    for key,g in df.groupby(['run','direction','step'],sort=False):
        g=g.sort_values('a_s')
        numeric=g[['v_before_V','v_after_V','a_s','b_s','i_A','u_i_A','leak_A','u_leak_ind_A','u_dt_s']].to_numpy(float)
        if not np.isfinite(numeric).all(): raise ValueError(f'{key}: nonfinite QS data')
        if (g[['u_i_A','u_leak_ind_A','u_dt_s']]<0).any().any(): raise ValueError('negative uncertainty')
        if g.v_before_V.nunique()!=1 or g.v_after_V.nunique()!=1: raise ValueError('inconsistent voltage')
        a=g.a_s.to_numpy(); b=g.b_s.to_numpy(); dt=b-a
        if len(a)<max(args.tail_points,8) or np.any(dt<=0) or a[0]<0 or np.any(a[1:]<b[:-1]-1e-12):
            raise ValueError(f'{key}: too few windows, negative start, or overlap')
        dv=float(g.v_after_V.iloc[0]-g.v_before_V.iloc[0])
        if not dv: raise ValueError('zero voltage step')
        y=(g.i_A-g.leak_A).to_numpy()
        ui=np.sqrt(g.u_i_A.to_numpy()**2+g.u_leak_ind_A.to_numpy()**2)
        if np.any(ui<=0): raise ValueError('positive independent current uncertainty required')
        pre=g.pre_settled.astype(str).str.lower().isin(['true','1']).all()
        gaps=[]; edge=0.0
        for l,r in zip(a,b):
            if l-edge>1e-12: gaps.append((edge,float(l)))
            edge=float(r)
        gap_duration=sum(r-l for l,r in gaps)
        n=args.tail_points; t=(a[-n:]+b[-n:])/2; yy=y[-n:]; uu=ui[-n:]
        X=np.column_stack([np.ones(n),t-t.mean()]); W=1/uu**2
        cov=np.linalg.inv(X.T@(W[:,None]*X)); beta=cov@(X.T@(W*yy))
        red_tail=float(np.sum(((X@beta-yy)/uu)**2)/max(1,n-2)); cov*=max(1,red_tail)
        tail_u=np.sqrt(cov[0,0]+args.u_current_common_A**2+args.u_leak_common_A**2)
        tail_ok=bool(abs(beta[0])+1.96*tail_u<=args.tail_limit_A and
                     abs(beta[1])+1.96*np.sqrt(cov[1,1])<=args.slope_limit_A_s)
        q=float(y@dt)
        uind=float(np.sqrt(np.sum((dt*ui)**2+(y*g.u_dt_s.to_numpy())**2)))
        duration=float(dt.sum())
        uq=float(np.sqrt(uind**2+(duration*args.u_current_common_A)**2+
                         (duration*args.u_leak_common_A)**2+(q*args.u_clock_rel)**2))
        flags=[]
        if not pre: flags.append('PRECONDITION_NOT_CONFIRMED')
        if not tail_ok: flags.append('TAIL_NOT_SETTLED')
        qgap=0.0; gapbound=0.0; fitinfo={}
        if args.model!='none':
            nexp=int(args.model[-1])
            if len(y)<=1+2*nexp: raise ValueError('insufficient samples for fit')
            par,pcov,red,cond,success=fit_exponential(a,b,y,ui,nexp)
            terms=sorted([(float(np.exp(par[2+2*m])),float(par[1+2*m]*np.exp(par[2+2*m]))) for m in range(nexp)])
            fitinfo=dict(parameters=par.tolist(),covariance=pcov.tolist(),reduced_chi2=red,
                         jacobian_condition=cond,success=success,
                         components_tau_s_Q_C=terms,fitted_residual_baseline_A=float(par[0]),
                         remaining_exponential_tail_C=float(sum(qc*np.exp(-b[-1]/tau) for tau,qc in terms)))
            if not success or red>5 or cond>1e8: flags.append('FIT_DIAGNOSTIC_WARNING')
        if gaps:
            if args.gap_mode=='reject': flags.append('GAPS_UNACCOUNTED')
            elif args.gap_mode=='bound':
                gapbound=gap_duration*args.gap_current_bound_A
                flags.append('GAPS_BOUNDED_NOT_CORRECTED')
            else:
                fun=lambda pp:sum(exp_integral(pp,l,r,nexp) for l,r in gaps)
                qgap=fun(par); fitinfo['u_gap_fit_only_C']=numerical_uncertainty(fun,par,pcov)
                flags.append('MODEL_DEPENDENT_GAP_CORRECTION')
        corrected=q+qgap
        c=corrected/dv
        uc=float(np.sqrt((uq/dv)**2+(corrected*args.u_dv_V/dv**2)**2))
        rows.append(dict(run=key[0],direction=key[1],step=key[2],
            v_mid_V=float((g.v_before_V.iloc[0]+g.v_after_V.iloc[0])/2),
            q_observed_C=q,u_q_observed_C=uq,q_gap_model_C=qgap,q_gap_abs_bound_C=gapbound,
            q_partial_or_corrected_C=corrected,c_apparent_F=c,u_c_observed_terms_F=uc,
            tail_current_A=beta[0],tail_slope_A_s=beta[1],tail_settled=tail_ok,pre_settled=pre,
            gap_duration_s=gap_duration,usable_for_pair=bool(pre and tail_ok and not gaps),
            status=';'.join(flags) or 'CHECKED_ASSUMPTIONS_STILL_REQUIRED'))
        details.append(dict(key=[str(x) for x in key],gaps_s=gaps,fit=fitinfo))
    return pd.DataFrame(rows),details

def analyse_hf(hfdf,tm,qsdf,args):
    if hfdf.empty: raise ValueError('no HF measurements')
    numeric=['hf_start_s','hf_end_s','frequency_Hz','g_S','b_S','vac_rms_DUT_V','vac_rms_source_V','vdc_DUT_V']
    if not np.isfinite(hfdf[numeric].to_numpy(float)).all(): raise ValueError('nonfinite HF data')
    if (hfdf.frequency_Hz<=0).any() or (hfdf.hf_end_s<=hfdf.hf_start_s).any(): raise ValueError('invalid HF timing/frequency')
    if ((tm.qs_end_s<=tm.qs_start_s)|(tm.plateau_end_s<=tm.plateau_start_s)|
        (tm.plateau_start_s<tm.qs_start_s)|(tm.plateau_end_s>tm.qs_end_s)).any():
        raise ValueError('invalid acquisition/plateau interval')
    merged=hfdf.merge(tm,on=KEY,how='left',validate='many_to_one',indicator=True)
    if (merged['_merge']!='both').any(): raise ValueError('HF step missing timing metadata')
    merged=merged.drop(columns='_merge').merge(qsdf,on=KEY,how='left',validate='many_to_one',indicator=True)
    if (merged['_merge']!='both').any(): raise ValueError('HF step missing QS result')
    merged=merged.drop(columns='_merge')
    rows=[]
    for _,r in merged.iterrows():
        flags=[]; eps=args.u_sync_s
        inside=r.hf_start_s-eps>=r.plateau_start_s and r.hf_end_s+eps<=r.plateau_end_s
        if not inside: flags.append('HF_NOT_CONTAINED_ON_PLATEAU')
        if str(r.ac_state).lower()!='on': flags.append('AC_STATE_NOT_CONFIRMED_ON')
        if abs(r.vdc_DUT_V-r.v_plateau_V)>args.max_vdc_error_V: flags.append('DC_BIAS_MISMATCH')
        if r.vac_rms_DUT_V<=0 or r.vac_rms_source_V<=0: flags.append('AC_LEVEL_INVALID')
        elif abs(r.vac_rms_DUT_V/r.vac_rms_source_V-1)>args.max_ac_load_error_rel:
            flags.append('AC_LEVEL_DIFFERS_FROM_SOURCE_SETTING')
        cp=r.b_S/(2*np.pi*r.frequency_Hz)
        if r.b_S<=0: flags.append('NONCAPACITIVE_OR_SIGN_CONVENTION')
        # The 20 kohm resistor is in the DC feed, NOT the LCR AC source path.
        # No AC divider or branch current is inferred from this resistor alone.
        row=r.to_dict(); row.update(cp_HF_F=cp,d_parallel=(r.g_S/r.b_S if r.b_S else np.nan),
            iac_rms_DUT_A=float(np.hypot(r.g_S,r.b_S)*r.vac_rms_DUT_V),
            r_bias_nominal_ohm=args.r_bias_ohm,
            joint_status=';'.join(flags) or 'HF_TIMING_AND_BIAS_CHECKS_PASSED',
            hf_inside_plateau=inside)
        rows.append(row)
    return pd.DataFrame(rows)

def parse_args():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--acq',type=Path); p.add_argument('--timing',type=Path); p.add_argument('--hf',type=Path)
    p.add_argument('--demo',action='store_true')
    p.add_argument('--model',choices=['none','exp1','exp2'],default='none')
    p.add_argument('--gap-mode',choices=['reject','bound','model'],default='reject')
    p.add_argument('--gap-current-bound-A',type=float)
    p.add_argument('--tail-points',type=int,default=10)
    p.add_argument('--tail-limit-A',type=float,default=1e-12)
    p.add_argument('--slope-limit-A-s',type=float,default=1e-10)
    p.add_argument('--u-current-common-A',type=float,default=0)
    p.add_argument('--u-leak-common-A',type=float,default=0)
    p.add_argument('--u-clock-rel',type=float,default=0)
    p.add_argument('--u-dv-V',type=float,default=0)
    p.add_argument('--derive-dut-voltage',action='store_true')
    p.add_argument('--r-bias-ohm',type=float,default=20000.0)
    p.add_argument('--u-r-bias-rel',type=float,default=.01)
    p.add_argument('--u-sync-s',type=float,default=0)
    p.add_argument('--max-vdc-error-V',type=float,default=.001)
    p.add_argument('--max-ac-load-error-rel',type=float,default=.1)
    p.add_argument('--output',type=Path,default=Path('qscv_final_results'))
    return p.parse_args()

def main():
    args=parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    if args.demo: paths=demo(args.output)
    else: paths=(args.acq,args.timing,args.hf)
    if any(x is None for x in paths): raise SystemExit('provide --acq --timing --hf or --demo')
    if args.r_bias_ohm<=0: raise SystemExit('positive R required')
    if any(v<0 for v in [args.u_current_common_A,args.u_leak_common_A,args.u_clock_rel,args.u_dv_V,args.u_r_bias_rel,args.u_sync_s,args.max_vdc_error_V,args.max_ac_load_error_rel]): raise SystemExit('negative uncertainty/tolerance')
    if args.tail_points<3 or args.tail_limit_A<=0 or args.slope_limit_A_s<=0: raise SystemExit('invalid tail limits')
    if args.gap_mode=='model' and args.model=='none': raise SystemExit('model gap correction needs exp1/exp2')
    if args.gap_mode=='bound' and (args.gap_current_bound_A is None or args.gap_current_bound_A<=0):
        raise SystemExit('bound mode requires positive --gap-current-bound-A')
    qsdf=read_csv(paths[0],REQ_QS); tm=read_csv(paths[1],REQ_TM); hfdf=read_csv(paths[2],REQ_HF)
    if tm.duplicated(KEY).any(): raise ValueError('duplicate timing step key')
    if args.derive_dut_voltage:
        cols=['vsource_before_V','vsource_after_V','ibias_before_A','ibias_after_A']
        if not set(cols)<=set(qsdf): raise ValueError('DUT voltage reconstruction requires '+str(cols))
        qsdf['v_before_V']=qsdf.vsource_before_V-args.r_bias_ohm*qsdf.ibias_before_A
        qsdf['v_after_V']=qsdf.vsource_after_V-args.r_bias_ohm*qsdf.ibias_after_A
    steps,details=analyse_qs(qsdf,args)
    steps.to_csv(args.output/'steps.csv',index=False)
    (args.output/'fits.json').write_text(json.dumps(details,indent=2),encoding='utf-8')
    joint=analyse_hf(hfdf,tm,steps,args)
    joint.to_csv(args.output/'joint_records.csv',index=False)
    group=KEY+['frequency_Hz','vac_rms_DUT_V','vdc_DUT_V']
    good=joint[(joint.joint_status=='HF_TIMING_AND_BIAS_CHECKS_PASSED') & joint.usable_for_pair]
    summary=good.groupby(group,dropna=False).agg(n_HF=('cp_HF_F','size'),cp_HF_mean_F=('cp_HF_F','mean'),
        cp_HF_repeat_sd_F=('cp_HF_F','std'),c_QS_associated_F=('c_apparent_F','first')).reset_index()
    summary.to_csv(args.output/'paired_summary.csv',index=False)
    pairs=[]
    for run,g in steps.groupby('run'):
        f=g[(g.direction=='forward') & g.usable_for_pair].sort_values('v_mid_V')
        r=g[(g.direction=='reverse') & g.usable_for_pair].sort_values('v_mid_V')
        for _,row in f.iterrows():
            x=row.v_mid_V
            if len(r)<2 or not(r.v_mid_V.min()<=x<=r.v_mid_V.max()): continue
            if r.v_mid_V.duplicated().any(): raise ValueError('duplicate reverse voltage; separate cycles into runs')
            reverse=float(np.interp(x,r.v_mid_V,r.c_apparent_F))
            pairs.append(dict(run=run,v_mid_V=x,c_forward_F=row.c_apparent_F,
                c_reverse_interpolated_F=reverse,delta_c_F=row.c_apparent_F-reverse))
    pd.DataFrame(pairs,columns=['run','v_mid_V','c_forward_F','c_reverse_interpolated_F','delta_c_F']).to_csv(args.output/'forward_reverse.csv',index=False)
    metadata=dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        warning='Offline analysis only. No acquisition driver. No automatic trap identification. No full covariance budget. 20k DC feed value is user supplied; verify schematic. No AC divider is inferred. Reconstructed voltage uncertainty requires an external correlated budget.')
    (args.output/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(args.output.resolve())

if __name__=='__main__': main()
