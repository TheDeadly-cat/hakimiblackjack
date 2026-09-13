"""Launch the actual 1x Tk preview with one explicitly selected local model."""
import argparse
import sys
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
    parser.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    parser.add_argument('--first-frame',type=int,default=0)
    parser.add_argument('--last-frame',type=int)
    parser.add_argument('--fps',type=float,default=8)
    parser.add_argument('--evidence-limit',type=int,default=1200,help='Bounded metadata rows; use a larger explicit bound for a complete long benchmark')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--close-after',type=float,help='Controlled UI check duration; does not speed up playback')
    args=parser.parse_args()
    from blackjack_lab.vision.model_adapter import TrainedModelAdapter,load_style
    from blackjack_lab.realtime_preview import RealtimeVideoSource,RealtimeWgcSource,RealtimePreviewSession
    from blackjack_lab.ui.realtime_preview import RealtimePreviewWindow
    style=load_style(args.style)
    baseline=TrainedModelAdapter(args.model,style_id=style.style_id)
    adapters={'旧提取器':baseline}
    if args.detector:
        from blackjack_lab.vision.rgb_corner_adapter import RgbCornerAdapter
        adapter=RgbCornerAdapter(args.model,args.detector,style_id=style.style_id,device=args.device)
        adapters['RGB 牌角']=adapter
    else:
        adapter=baseline
    source=(RealtimeVideoSource(args.video,style,first_frame=args.first_frame,last_frame=args.last_frame)
            if args.video else RealtimeWgcSource(args.window,style))
    session=RealtimePreviewSession(source,style,adapter,recognition_fps=args.fps,evidence_limit=args.evidence_limit)
    root=tk.Tk();root.withdraw()
    window=RealtimePreviewWindow(root,session,adapters=adapters)
    def close():
        window.close()
        # The UI itself never waits for a worker to join.
        root.quit()
    window.protocol('WM_DELETE_WINDOW',close)
    if args.close_after:
        window.after(round(args.close_after*1000),close)
    root.mainloop()
    session=window.session
    if session._thread:
        session._thread.join(timeout=3)
    if args.output:
        session.save(args.output)
    root.destroy()
    return 1 if session.error or session.source.error else 0

if __name__=='__main__':
    raise SystemExit(main())
