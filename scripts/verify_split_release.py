"""Fixed-source V0.2b1 acceptance. Every invocation keeps unique raw outputs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest, sha


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--compare-dir',type=Path)
    args=parser.parse_args()
    output=(args.output or ROOT/'.local-evidence'/('acceptance-v02b1-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])).resolve()
    output.mkdir(parents=True,exist_ok=False)
    before=source_manifest()
    identity=source_identity(ROOT)
    environment=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    checks=[]

    def run(name,arguments,timeout=180,env=None):
        started=time.perf_counter()
        command=[sys.executable,*arguments]
        try:
            result=subprocess.run(command,cwd=ROOT,env=env or environment,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,encoding='utf-8',timeout=timeout)
            code,text=result.returncode,result.stdout
        except subprocess.TimeoutExpired as error:
            code=None
            text=error.stdout or ''
            if isinstance(text,bytes):
                text=text.decode('utf-8',errors='replace')
            text+='\nACCEPTANCE COMMAND TIMEOUT\n'
        path=output/(name+'.txt')
        path.write_text(text,encoding='utf-8')
        checks.append(dict(name=name,command=command,exit_code=code,elapsed_seconds=time.perf_counter()-started,
                           output=path.name,sha256=sha(path)))
        print(f"{name}: exit={code} elapsed={checks[-1]['elapsed_seconds']:.3f}s",flush=True)
        if code!=0:
            print(text[-3500:],flush=True)

    run('selfcheck',['-m','blackjack_lab.main','--check'])
    run('prepare-native',['-m','blackjack_lab.main','--prepare-split'])
    run('full-tests',['-m','unittest','discover','-s','tests','-v'])
    run('compile',['-m','compileall','-q','blackjack_lab','tests','scripts'])
    run('original-5-48-4',['scripts/verify_review_handoff.py','--output',str(output/'original-5-48-4')])
    run('original-n1-four',['-m','unittest','discover','-s','docs/acceptance/n1-20260910-220300-9f6f68/original','-p','test_snapshot_shape.py','-v'])
    with tempfile.TemporaryDirectory() as temporary:
        guard=Path(temporary)/'sitecustomize.py'
        guard.write_text("""import os,socket
from pathlib import Path
with Path(os.environ['HAKIMI_GUARD_LOG']).open('a',encoding='utf-8') as stream: stream.write(str(os.getpid())+'\\n')
def blocked(*args,**kwargs):
    with Path(os.environ['HAKIMI_ATTEMPT_LOG']).open('a',encoding='utf-8') as stream: stream.write('blocked network call\\n')
    raise RuntimeError('Python network blocked by acceptance guard')
socket.create_connection=blocked
socket.getaddrinfo=blocked
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket._hakimi_offline_guard=True
""",encoding='utf-8')
        env=dict(environment,PYTHONPATH=temporary+os.pathsep+str(ROOT),HAKIMI_OFFLINE_REQUIRED='1',
                 HAKIMI_GUARD_LOG=str(output/'guard-processes.txt'),HAKIMI_ATTEMPT_LOG=str(output/'network-attempts.txt'))
        run('guarded-split-workflows',['-m','unittest','tests.test_split_workflow','tests.test_snapshot_shape','-v'],env=env)
    run('split-ui',['scripts/split_ui_check.py','--output',str(output/'screens')])
    performance=['scripts/split_benchmarks.py','--output',str(output/'performance')]
    if args.compare_dir:
        performance.extend(['--compare-dir',str(args.compare_dir.resolve())])
    run('cold-performance',performance,timeout=900)
    receipt=dict(schema='hakimi-v02b1-acceptance-v1',timestamp_utc=datetime.now(timezone.utc).isoformat(),
        identity=identity,python=sys.version,platform=platform.platform(),source_manifest=before,
        source_unchanged=before==source_manifest(),checks=checks,user_database_accessed=False,
        data_scope='Only temporary SQLite and explicitly synthetic input fixtures',
        network_scope='Python socket guard includes spawned Python workers; native code uses local stdin/stdout only. This is not an OS firewall or .NET network-blocking test.',
        network_attempts=(output/'network-attempts.txt').read_text(encoding='utf-8') if (output/'network-attempts.txt').exists() else '')
    receipt['passed']=all(c['exit_code']==0 for c in checks) and receipt['source_unchanged'] and not receipt['network_attempts']
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'evidence-manifest.json').write_text(json.dumps({p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),passed=receipt['passed'],identity=identity),ensure_ascii=False),flush=True)
    return 0 if receipt['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
