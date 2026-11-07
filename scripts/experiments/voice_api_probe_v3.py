"""Controlled audio sensitivity pilot, four blinded new samples, no retries.

V1/V2 are immutable. Reuses exactly V2 system prompt and metadata fields.
All audio processing is local signal processing, never ASR/TTS or another model.
"""
from __future__ import annotations
import argparse,base64,json,math,os,runpy,sqlite3,urllib.request,urllib.error,wave
from pathlib import Path
from decimal import Decimal
import numpy as np
from scipy.signal import correlate

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'tmp/voice-api-experiment-v3'
V2=ROOT/'tmp/voice-api-experiment-v2'
HELP=runpy.run_path(str(ROOT/'scripts/experiments/voice_api_probe_v2.py'))
sha,read,write,now,validate=[HELP[k] for k in ['sha','read','write','now','validate']]
PROMPT=(V2/'prompt.txt').read_bytes().decode('utf-8')
MODEL='gpt-audio-1.5';ENDPOINT='https://api.openai.com/v1/chat/completions'
CAP=Decimal('0.30');RESERVE=Decimal('0.094');MAX_OUTPUT=1000

def usage_cost(u):
    ai=u['prompt_tokens_details']['audio_tokens'];ti=u['prompt_tokens']-ai;po=u['completion_tokens']
    if not all(type(v) is int and v>=0 for v in [ai,ti,po]) or u['completion_tokens_details']['audio_tokens']!=0:raise ValueError('Incomplete usage')
    if u['prompt_tokens_details'].get('text_tokens',ti)!=ti or u['completion_tokens_details'].get('text_tokens',po)!=po:raise ValueError('Breakdown mismatch')
    return (Decimal(ai)*32+Decimal(ti)*Decimal('2.5')+Decimal(po)*10)/Decimal(1000000)

def prior_cost():
    total=Decimal(0)
    for folder in ['voice-api-experiment','voice-api-experiment-v2']:
        ledger=read(ROOT/'tmp'/folder/'ledger.json')
        if len(ledger['attempts'])!=3:raise RuntimeError('Unexpected previous attempt count')
        for a in ledger['attempts']:
            if a['state']!='usage_accounted':raise RuntimeError('Unsettled previous round')
            cost=usage_cost(a['usage'])
            if cost!=Decimal(a['actual_cost_usd']):raise RuntimeError('Previous accounting mismatch')
            total+=cost
    if total!=Decimal('0.0769245'):raise RuntimeError('Previous total changed')
    return total

def held(a):
    return Decimal(a['actual_cost_usd']) if a['state']=='usage_accounted' else max(Decimal(a['reserve_usd']),Decimal(a.get('actual_cost_usd') or '0'))

def wav_read(path):
    with wave.open(str(path),'rb') as f:
        assert f.getnchannels()==1 and f.getsampwidth()==2
        return np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').copy(),f.getframerate()

def wav_write(path,values,rate):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(np.asarray(values,dtype='<i2').tobytes())

def wsola(x,factor,rate=16000):
    """Waveform-similarity overlap-add: no sample-rate change or pitch shift.

    40ms frames, about20ms synthesis hops, +/-10ms similarity search. The first
    and last frames are pinned to source boundaries. A weighted crossfade merges
    overlapping unresampled waveform segments. Artifacts remain possible.
    """
    x=np.asarray(x,dtype=np.float64);length=round(.04*rate);hop=round(.02*rate);search=round(.01*rate)
    target=round(len(x)/factor);steps=math.ceil((target-length)/hop)
    positions=np.rint(np.linspace(0,target-length,steps+1)).astype(int)
    output=np.zeros(target);output[:length]=x[:length];mapping=[{'output_frame':0,'source_frame':0}]
    previous=0
    for index,position in enumerate(positions[1:],1):
        overlap=previous+length-position
        expected=round(position/(target-length)*(len(x)-length))
        lo=max(0,expected-search);hi=min(len(x)-length,expected+search)
        if index==steps:chosen=len(x)-length
        else:
            reference=output[position:position+overlap];candidates=x[lo:hi+overlap]
            numerator=correlate(candidates,reference,mode='valid',method='fft')
            energy=np.convolve(candidates*candidates,np.ones(overlap),mode='valid')
            similarity=numerator/np.sqrt(np.maximum(energy*np.dot(reference,reference),1e-20))
            chosen=lo+int(np.argmax(similarity))
        segment=x[chosen:chosen+length];weight=np.linspace(0,1,overlap)
        output[position:position+overlap]=output[position:position+overlap]*(1-weight)+segment[:overlap]*weight
        output[position+overlap:position+length]=segment[overlap:];previous=position
        mapping.append({'output_frame':int(position),'source_frame':int(chosen)})
    return output,mapping

def f0(x,rate):
    """Rough autocorrelation diagnostic, not phonetic truth."""
    estimates=[]
    for i in range(0,len(x)-int(.04*rate),int(.02*rate)):
        frame=np.array(x[i:i+int(.04*rate)],dtype=float);frame-=frame.mean()
        if np.sqrt(np.mean(frame*frame))<.02:continue
        ac=correlate(frame,frame,mode='full',method='fft')[len(frame)-1:]
        low,high=round(rate/350),round(rate/70);lag=low+np.argmax(ac[low:high])
        if ac[lag]/max(ac[0],1e-12)>.5:estimates.append(rate/lag)
    return {'median_hz':float(np.median(estimates)) if estimates else None,'accepted_frames':len(estimates)}

def prepare():
    if (OUT/'ledger.json').exists():raise RuntimeError('Never reset existing V3')
    prior=prior_cost();samples=[];pair_info=[]
    voicebank=[]
    for sid,condition in [('A','clean'),('B','noisy')]:
        source=OUT/(condition+'-p232_021.wav');data,rate=wav_read(source);dest=OUT/(sid+'.wav')
        # Preserve the published waveform and sample rate exactly; no resampling.
        dest.write_bytes(source.read_bytes());voicebank.append(data.astype(float)/32768)
        samples.append({'sample_id':sid,'file':dest.name,'sha256':sha(dest.read_bytes()),'duration_s':len(data)/rate,'sample_rate':rate,
          'source_type':'external_read_speech','source_file':source.relative_to(ROOT).as_posix(),'source_sha256':sha(source.read_bytes()),
          'license':'CC-BY-4.0','attribution':'Cassia Valentini-Botinhao, University of Edinburgh,2017,doi:10.7488/ds/2117',
          'source_page':'https://datashare.ed.ac.uk/handle/10283/2791','published_record':'p232_021.wav','condition_private':condition})
    clean,noisy=voicebank;assert len(clean)==len(noisy)
    gain=float(np.dot(clean,noisy)/np.dot(clean,clean));residual=noisy-gain*clean
    pair_info.append({'condition':'noise','original_sample_id':'A','variant_sample_id':'B','published_noise':'psquare','published_snr_db':12.5,
      'measured_scale_fit':gain,'measured_global_residual_snr_db':float(10*np.log10(np.mean((gain*clean)**2)/np.mean(residual**2))),
      'measurement_limitation':'Global least-squares residual SNR is not the publication speech-active normalization and may include source scaling/quantization.',
      'expected_target':'line_quality: audible background noise, distinct from articulation','known_change':'Published clean/noisy parallel pair, identical frame count; actual human speech, additive published noise.'})
    source=V2/'kz-fleurs-full.wav';data,rate=wav_read(source);point=round(6.4*rate);insert=round(2.5*rate)
    variant=np.concatenate([data[:point],np.zeros(insert,dtype=np.int16),data[point:]])
    assert np.array_equal(variant[:point],data[:point]) and np.array_equal(variant[point+insert:],data[point:])
    wav_write(OUT/'C.wav',variant,rate)
    quiet=data[round(6.1*rate):round(6.65*rate)].astype(float)/32768
    samples.append({'sample_id':'C','file':'C.wav','sha256':sha((OUT/'C.wav').read_bytes()),'duration_s':len(variant)/rate,'sample_rate':rate,
      'source_type':'external_read_speech','source_file':source.relative_to(ROOT).as_posix(),'source_sha256':sha(source.read_bytes()),
      'license':'CC-BY-4.0','attribution':'FLEURS,Conneau et al.,Google2022; distributed through MTEB','condition_private':'laboratory inserted pause'})
    pair_info.append({'condition':'pause','original_answer_file':'tmp/voice-api-experiment-v2/response-kz.raw.json','original_audio_file':source.relative_to(ROOT).as_posix(),
      'variant_sample_id':'C','insert_at_s':6.4,'inserted_seconds':2.5,'inserted_frames':insert,'known_interval_variant_s':[6.4,8.9],
      'existing_quiet_interval_s':[6.06,6.72],'surrounding_rms_6_1_to_6_65':float(np.sqrt(np.mean(quiet**2))),
      'sample_before_insert':int(data[point-1]),'sample_after_insert':int(data[point]),'prefix_suffix_bit_identical':True,'removed_source_frames':0,
      'expected_target':'pauses: prolonged interruption localized near inserted quiet interval',
      'limitation':'Inserted within an existing low-energy interval; no source samples removed. Inter-word location inferred from silence, not independently phoneme-aligned/listened. Not a natural speaker defect.'})
    source=V2/'en-fleurs-full.wav';data,rate=wav_read(source);factor=1.8
    y,mapping=wsola(data.astype(float)/32768,factor,rate)
    assert np.max(np.abs(y))<1
    pcm=np.rint(y*32768).astype('<i2');wav_write(OUT/'D.wav',pcm,rate)
    # One diagnostic checks pitch preservation algorithmically, without new speech.
    tone=.3*np.sin(2*np.pi*200*np.arange(2*rate)/rate);tone_y,_=wsola(tone,factor,rate)
    spectrum=np.abs(np.fft.rfft(tone_y*np.hanning(len(tone_y))));peak=float(np.fft.rfftfreq(len(tone_y),1/rate)[np.argmax(spectrum)])
    assert abs(peak-200)<2
    write(OUT/'wsola-frame-map.json',mapping)
    samples.append({'sample_id':'D','file':'D.wav','sha256':sha((OUT/'D.wav').read_bytes()),'duration_s':len(pcm)/rate,'sample_rate':rate,
      'source_type':'external_read_speech','source_file':source.relative_to(ROOT).as_posix(),'source_sha256':sha(source.read_bytes()),
      'license':'CC-BY-4.0','attribution':'FLEURS,Conneau et al.,Google2022','condition_private':'laboratory WSOLA1.8x'})
    pair_info.append({'condition':'fast','original_answer_file':'tmp/voice-api-experiment-v2/response-en.raw.json','original_audio_file':source.relative_to(ROOT).as_posix(),
      'variant_sample_id':'D','speed_factor_requested':factor,'measured_duration_ratio':len(data)/len(pcm),'original_duration_s':len(data)/rate,
      'variant_duration_s':len(pcm)/rate,'algorithm':'Local WSOLA waveform-similarity overlap-add,40ms frames,20ms synthesis hops,+/-10ms similarity search; first/last source frames pinned',
      'no_resampling_for_speed':True,'tone200hz_output_peak_hz':peak,'source_f0_diagnostic':f0(data.astype(float)/32768,rate),'variant_f0_diagnostic':f0(y,rate),
      'expected_target':'pace: detect substantial acceleration/fast speech relative to original, without claiming universal language threshold',
      'limitation':'Laboratory acceleration, not naturally excessive speech. WSOLA artifacts and pre-existing EN tail noise may confound. Pitch diagnostic is not full perceptual verification.'})
    for s in samples:
        pcm,rate=wav_read(OUT/s['file']);s['clipped_samples']=int(np.sum((pcm==-32768)|(pcm==32767)));s['peak_normalized']=float(np.max(np.abs(pcm.astype(float)))/32768)
        assert s['clipped_samples']==0
    write(OUT/'manifest.json',{'created_at':now(),'samples':samples,'pairs':pair_info,'independent_listening':False,'source_references':'V1/V2 manifests retain FLEURS asset URLs/licenses; VoiceBank acquisition records retain ZIP paths/CRC.'})
    (OUT/'prompt.txt').write_bytes((V2/'prompt.txt').read_bytes())
    prior_hashes={folder:sha((ROOT/'tmp'/folder/'ledger.json').read_bytes()) for folder in ['voice-api-experiment','voice-api-experiment-v2']}
    write(OUT/'budget-plan.json',{'created_at':now(),'prior_settled_usd':str(prior),'remaining_usd':str(CAP-prior),'combined_cap_usd':str(CAP),
      'planned_new_samples':['A','B','C','D'],'max_new_attempts':4,'authorized_ceiling':6,'max_retries':0,'reserve_each_usd':str(RESERVE),
      'reserve_bounds':{'audio_input_tokens':2000,'text_input_tokens':8000,'text_output_tokens':1000},'prices_per_million':{'audio_input':32,'text_input':'2.50','text_output':10},
      'bound_is_billing_guarantee':False,'basis':'V1/V2 measured about10 audio tokens/s; maximum17.38s here gives174 reference tokens versus2000 reserved. Fixed V2 prompt. Output API limit1000.',
      'budget_rule':'Verified settled V1+V2+settled V3+unknown/active reserves+next reserve<=0.30. Sequential; stop on uncertain outcome. Settled reserves replaced by actual cost.',
      'prompt_sha256':sha(PROMPT.encode()),'manifest_sha256':sha((OUT/'manifest.json').read_bytes()),'prior_ledger_hashes':prior_hashes,
      'tariffs_checked_date':'2026-11-08 Asia/Qyzylorda','tariff_source':'https://developers.openai.com/api/docs/models/gpt-audio-1.5'})
    write(OUT/'expectations.json',{'created_at':now(),'frozen_before_requests':True,'pairs':pair_info,
      'detection_rule':'Report both target-property recognition and explicit issue_suspected separately. Target-positive requires relevant audible-event/property; semantic review required. A status alone is insufficient.',
      'collateral_checks':['articulation attribution despite only noise/pause/tempo manipulation','unsupported new defects','false target claims in original','evidence within duration','evidence near known affected region'],
      'reuse':'Same V2 system prompt and metadata fields; existing KZ/EN original responses reused across rounds, no variance recheck. No selection/rerun based on outputs.'})
    write(OUT/'ledger.json',{'created_at':now(),'attempts':[],'combined_cap_usd':str(CAP),'prior_settled_usd':str(prior)})
    print(json.dumps({'samples':[(s['sample_id'],s['duration_s']) for s in samples],'transforms':pair_info,'prior_spend':str(prior)},ensure_ascii=False))

def run(sid):
    lock=OUT/'sequential.lock';gfd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);fd=None
    try:
        fd=os.open(OUT/('run-'+sid+'.lock'),os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,now().encode());os.fsync(fd)
        ledger=read(OUT/'ledger.json');attempts=ledger['attempts'];plan=read(OUT/'budget-plan.json')
        if len(attempts)>=4 or any(a['sample_id']==sid for a in attempts):raise RuntimeError('No repeats; attempt cap')
        if any(a['state']!='usage_accounted' for a in attempts):raise RuntimeError('Unresolved prior call')
        for filename,key in [('manifest.json','manifest_sha256'),('prompt.txt','prompt_sha256')]:
            if sha((OUT/filename).read_bytes())!=plan[key]:raise RuntimeError('Frozen input changed')
        if sha(PROMPT.encode())!=plan['prompt_sha256']:raise RuntimeError('V2 prompt changed')
        for folder,h in plan['prior_ledger_hashes'].items():
            if sha((ROOT/'tmp'/folder/'ledger.json').read_bytes())!=h:raise RuntimeError('Previous ledger changed')
        prior=prior_cost();spent=sum((held(a) for a in attempts),Decimal(0));committed=prior+spent+RESERVE
        if committed>CAP:raise RuntimeError('Combined cap')
        journal=[json.loads(x) for x in (ROOT/'local-data/openai-usage.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
        if any(r.get('cost_usd') is None for r in journal):raise RuntimeError('Unknown product spend')
        legacy=max(Decimal('0.09511375'),sum((Decimal(str(r['cost_usd'])) for r in journal),Decimal(0)))
        if legacy+committed>5:raise RuntimeError('Global cap')
        c=next(c for c in read(OUT/'manifest.json')['samples'] if c['sample_id']==sid);audio=(OUT/c['file']).read_bytes()
        if sha(audio)!=c['sha256']:raise RuntimeError('Clip changed')
        key=runpy.run_path(str(ROOT/'scripts/experiments/voice_api_probe.py'))['get_key']()
        metadata={'duration_s':c['duration_s'],'source_type':c['source_type'],'timestamps':'approximate local seconds'}
        payload={'model':MODEL,'modalities':['text'],'max_completion_tokens':MAX_OUTPUT,'n':1,'stream':False,'store':False,
          'messages':[{'role':'system','content':PROMPT},{'role':'user','content':[{'type':'text','text':json.dumps(metadata)},
          {'type':'input_audio','input_audio':{'data':base64.b64encode(audio).decode(),'format':'wav'}}]}]}
        body=json.dumps(payload).encode()
        write(OUT/('request-'+sid+'-metadata.json'),{'model':MODEL,'metadata':metadata,'prompt_sha256':sha(PROMPT.encode()),'request_sha256':sha(body),'clip_sha256':c['sha256'],'max_retries':0})
        a={'sample_id':sid,'state':'reserved_unknown','reserve_usd':str(RESERVE),'actual_cost_usd':None,'accounted_usd':str(RESERVE),
           'reserved_at':now(),'prior_settled_usd':str(prior),'v3_committed_before_usd':str(spent),'all_rounds_committed_with_reserve_usd':str(committed)}
        attempts.append(a);write(OUT/'ledger.json',ledger)
        request=urllib.request.Request(ENDPOINT,data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.build_opener(HELP['NoRedirect']()).open(request,timeout=55) as r:raw=r.read();status=r.status;rid=r.headers.get('x-request-id')
        except urllib.error.HTTPError as e:raw=e.read();status=e.code;rid=e.headers.get('x-request-id')
        except Exception as e:
            a.update(state='uncertain',error_type=type(e).__name__);write(OUT/'ledger.json',ledger);print(json.dumps(a));return
        (OUT/('response-'+sid+'.raw.json')).write_bytes(raw);a.update(http_status=status,request_id=rid,raw_sha256=sha(raw),finished_at=now())
        try:
            response=json.loads(raw);a.update(response_id=response.get('id'),response_model=response.get('model'),usage=response.get('usage'))
            if status!=200 or (response.get('model')!=MODEL and not response.get('model','').startswith(MODEL+'-')):raise ValueError('Response/model mismatch')
            u=response['usage'];cost=usage_cost(u);ai=u['prompt_tokens_details']['audio_tokens'];ti=u['prompt_tokens']-ai;po=u['completion_tokens']
            a.update(state='usage_accounted',actual_cost_usd=str(cost),accounted_usd=str(cost),input_audio_tokens=ai,input_text_tokens=ti,output_text_tokens=po)
            if ai>2000 or ti>8000 or po>MAX_OUTPUT or cost>RESERVE:a.update(state='bound_exceeded',accounted_usd=str(max(cost,RESERVE)))
            choice=response['choices'][0];a['finish_reason']=choice['finish_reason'];text=choice['message']['content'];(OUT/('answer-'+sid+'.txt')).write_text(text,encoding='utf-8')
            try:parsed=json.loads(text);write(OUT/('answer-'+sid+'.parsed.json'),parsed);errors=validate(parsed,c['duration_s'])
            except (ValueError,KeyError,TypeError,AttributeError) as e:errors=[type(e).__name__]
            write(OUT/('validation-'+sid+'.json'),{'schema_errors':errors,'finish_reason':choice['finish_reason'],'independent_listening':False})
        except (ValueError,KeyError,TypeError) as e:a.update(state='uncertain',accounted_usd=str(RESERVE),error_type=type(e).__name__)
        write(OUT/'ledger.json',ledger);print(json.dumps(a))
    finally:
        if fd is not None:os.close(fd)
        os.close(gfd);lock.unlink()

def verify():
    base=read(OUT/'baseline.json');changed=[p for p,h in base['files'].items() if not (ROOT/p).exists() or sha((ROOT/p).read_bytes())!=h]
    db=sqlite3.connect((ROOT/'local-data/cqa.sqlite').as_uri()+'?mode=ro',uri=True);rows=db.execute('SELECT id,body FROM evaluations ORDER BY id').fetchall();db.close()
    checks={};errors={};manifest=read(OUT/'manifest.json')
    for c in manifest['samples']:
        pcm,rate=wav_read(OUT/c['file']);checks[c['sample_id']]=len(pcm)/rate==c['duration_s'] and sha((OUT/c['file']).read_bytes())==c['sha256'] and sha((ROOT/c['source_file']).read_bytes())==c['source_sha256']
        path=OUT/('answer-'+c['sample_id']+'.parsed.json')
        if path.exists():errors[c['sample_id']]=validate(read(path),c['duration_s'])
    attempts=read(OUT/'ledger.json')['attempts'];v3=sum((held(a) for a in attempts),Decimal(0));total=prior_cost()+v3
    report={'checked_at':now(),'protected_files_checked':len(base['files']),'protected_files_changed':changed,'evaluation_count':len(rows),
      'evaluation_rows_unchanged':sha(json.dumps(rows,ensure_ascii=False).encode())==base['evaluation_rows_sha256'],'clips_ok':checks,'schema_errors':errors,
      'v3_attempts':len(attempts),'v3_accounted_usd':str(v3),'all_rounds_accounted_usd':str(total),'remaining_usd':str(CAP-total),
      'all_attempts_settled':all(a['state']=='usage_accounted' for a in attempts),'prompt_equals_v2':(OUT/'prompt.txt').read_bytes()==(V2/'prompt.txt').read_bytes()}
    write(OUT/'verification.json',report);print(json.dumps(report))
    if changed or not report['evaluation_rows_unchanged'] or not all(checks.values()) or any(errors.values()) or total>CAP:raise SystemExit(1)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','verify']);p.add_argument('--sample',choices=['A','B','C','D']);args=p.parse_args()
    if args.action=='run':
        if not args.sample:p.error('--sample required')
        run(args.sample)
    else:{'prepare':prepare,'verify':verify}[args.action]()
