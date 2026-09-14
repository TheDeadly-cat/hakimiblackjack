"""Launch the actual 1x Tk preview with one explicitly selected local model."""
import argparse
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source_group=parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--video',type=Path)
    source_group.add_argument('--window',type=int,help='Explicitly selected authorized HWND')
    parser.add_argument('--style',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--detector',type=Path,help='Explicit experimental RGB detector directory; omitted uses old extraction')
    parser.add_argument('--cnn-model',type=Path,help='Optional frozen independent CNN rank model, sharing the same RGB detector')
    parser.add_argument('--rgb-tiles',action='store_true',help='Add fixed native 320px RGB context tiles with unchanged weights')
    parser.add_argument('--rgb-otsu',action='store_true',help='Add experimental local grayscale extraction to native tiled RGB')
    parser.add_argument('--initial-method',choices=('baseline','rgb','rgb-cnn','rgb-tiled','rgb-tiled-otsu'),help='Explicit initial selection; new experiments are not selected automatically')
    parser.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    parser.add_argument('--first-frame',type=int,default=0)
    parser.add_argument('--last-frame',type=int)
    parser.add_argument('--fps',type=float,default=8)
    parser.add_argument('--evidence-limit',type=int,default=1200,help='Bounded metadata rows; use a larger explicit bound for a complete long benchmark')
    parser.add_argument('--disable-region-observation',action='store_true',help='Explicit legacy fixed-rate comparison; default observes native regions and skips only exact previously inferred pixels')
    parser.add_argument('--temporal-policy',choices=('distinct-crops','current-observations'),default='distinct-crops',
                        help='Explicit preview-only consistency comparison; full-source repeats still cannot refresh state')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--close-after',type=float,help='Controlled UI check duration; does not speed up playback')
    parser.add_argument('--close-on-finish',action='store_true',help='Close after the full source and final expiry repaint; never accelerates playback')
    parser.add_argument('--geometry',help='Optional Tk window geometry, for example 2000x1120 for native ROI display')
    args=parser.parse_args()
    if args.initial_method=='rgb' and not args.detector:
        parser.error('--initial-method rgb requires --detector')
    if args.cnn_model and not args.detector:parser.error('--cnn-model requires --detector')
    if args.initial_method=='rgb-cnn' and not args.cnn_model:parser.error('--initial-method rgb-cnn requires --cnn-model')
    if args.rgb_tiles and not args.detector:parser.error('--rgb-tiles requires --detector')
    if args.initial_method=='rgb-tiled' and not args.rgb_tiles:parser.error('--initial-method rgb-tiled requires --rgb-tiles')
    if args.rgb_otsu and not args.rgb_tiles:parser.error('--rgb-otsu requires --rgb-tiles')
    if args.initial_method=='rgb-tiled-otsu' and not args.rgb_otsu:
        parser.error('--initial-method rgb-tiled-otsu requires --rgb-otsu')
    from blackjack_lab.vision.model_adapter import TrainedModelAdapter,load_style
    from blackjack_lab.realtime_preview import RealtimeVideoSource,RealtimeWgcSource,RealtimePreviewSession
    from blackjack_lab.ui.realtime_preview import RealtimePreviewWindow
    from blackjack_lab.vision.temporal_preview import POLICY_VERSION,PERSISTENT_OBSERVATION_POLICY
    style=load_style(args.style)
    baseline=TrainedModelAdapter(args.model,style_id=style.style_id)
    adapters={'旧提取器':baseline}
    if args.detector:
        from blackjack_lab.vision.rgb_corner_adapter import RgbCornerAdapter
        adapter=RgbCornerAdapter(args.model,args.detector,style_id=style.style_id,device=args.device)
        adapters['RGB 牌角']=adapter
    else:
        adapter=baseline
    if args.cnn_model:
        adapters['RGB + CNN']=adapter.with_rank_model(args.cnn_model,classifier_device=args.device)
        if args.initial_method=='rgb-cnn':adapter=adapters['RGB + CNN']
    if args.rgb_tiles:
        from blackjack_lab.vision.rgb_corner_model import TILED_POLICY
        adapters['RGB 原生分块']=adapters['RGB 牌角'].with_inference_policy(TILED_POLICY)
        if args.initial_method=='rgb-tiled':adapter=adapters['RGB 原生分块']
    if args.rgb_otsu:
        from blackjack_lab.vision.rgb_corner_adapter import NAVY_OTSU_PREPROCESSING
        adapters['RGB 分块灰度']=adapters['RGB 原生分块'].with_crop_preprocessing(NAVY_OTSU_PREPROCESSING)
        if args.initial_method=='rgb-tiled-otsu':adapter=adapters['RGB 分块灰度']
    if args.initial_method=='baseline':adapter=baseline
    source=(RealtimeVideoSource(args.video,style,first_frame=args.first_frame,last_frame=args.last_frame)
            if args.video else RealtimeWgcSource(args.window,style))
    session=RealtimePreviewSession(source,style,adapter,recognition_fps=args.fps,evidence_limit=args.evidence_limit,
        observe_regions=not args.disable_region_observation,
        temporal_policy=PERSISTENT_OBSERVATION_POLICY if args.temporal_policy=='current-observations' else POLICY_VERSION)
    root=tk.Tk();root.withdraw()
    window=RealtimePreviewWindow(root,session,adapters=adapters)
    if args.geometry:window.geometry(args.geometry)
    closing=False
    shutdown_error=None
    def close():
        nonlocal closing,shutdown_error
        if closing:return
        closing=True
        window.withdraw()
        window.stop()
        deadline=time.perf_counter()+5
        def finish_when_released():
            nonlocal shutdown_error
            current=window.session
            if current.finished and current.source.finished:
                window.close();root.quit()
            elif time.perf_counter()>=deadline:
                shutdown_error='预览已关闭，但后台来源未在 5 秒内完成释放；本轮清理未通过。'
                window.close();root.quit()
            else:
                # WGC can need native messages during release. Never stop the
                # Tk pump first or block its thread on a background join.
                root.after(25,finish_when_released)
        root.after(0,finish_when_released)
    window.protocol('WM_DELETE_WINDOW',close)
    if args.close_after:
        window.after(round(args.close_after*1000),close)
    if args.close_on_finish:
        def check_finished():
            if window.session.finished and window.session.source.finished:
                window.after(800,close)
            else:
                window.after(100,check_finished)
        window.after(100,check_finished)
    root.mainloop()
    session=window.session
    if session._thread:
        session._thread.join(timeout=3)
    if getattr(session.source,'_thread',None):
        session.source._thread.join(timeout=3)
    if shutdown_error and not session.error:session.error=shutdown_error
    if args.output:
        session.save(args.output)
    root.destroy()
    return 1 if session.error or session.source.error else 0

if __name__=='__main__':
    raise SystemExit(main())
