"""Launch the actual 1x Tk preview with one explicitly selected local model."""
import argparse
import sys
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',required=True,type=Path)
    parser.add_argument('--style',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--first-frame',type=int,default=0)
    parser.add_argument('--last-frame',type=int)
    parser.add_argument('--fps',type=float,default=8)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--close-after',type=float,help='Controlled UI check duration; does not speed up playback')
    args=parser.parse_args()
    from blackjack_lab.vision.model_adapter import TrainedModelAdapter,load_style
    from blackjack_lab.realtime_preview import RealtimeVideoSource,RealtimePreviewSession
    from blackjack_lab.ui.realtime_preview import RealtimePreviewWindow
    style=load_style(args.style)
    adapter=TrainedModelAdapter(args.model,style_id=style.style_id)
    source=RealtimeVideoSource(args.video,style,first_frame=args.first_frame,last_frame=args.last_frame)
    session=RealtimePreviewSession(source,style,adapter,recognition_fps=args.fps)
    root=tk.Tk();root.withdraw()
    window=RealtimePreviewWindow(root,session)
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
