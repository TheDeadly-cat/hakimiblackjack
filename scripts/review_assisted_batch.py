"""Check prefilled local video pages: change labels, delete false boxes, approve a page."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.frame_annotations import read_frame
from blackjack_lab.vision.glyph_dataset import LABEL_RANKS

LABELS = LABEL_RANKS + ("unreadable",)


class ReviewStore:
    def __init__(self, bundle):
        self.path = Path(bundle)
        self.bundle = json.loads(self.path.read_text(encoding="utf-8"))
        if self.bundle.get("schema") != "assisted-review-bundle-1":
            raise ValueError("不是批量复核清单")
        self.entries = self.bundle["sessions"]
        if not self.entries:
            raise ValueError("批量复核清单为空")
        self.documents, self.digests = [], []
        for entry in self.entries:
            raw = Path(entry["annotations"]).read_bytes()
            data = json.loads(raw)
            if data.get("schema") != "original-frame-annotations-1" or not data.get("frames"):
                raise ValueError("标注格式不符或没有页面")
            if data.get("source_sha256") != entry["source_sha256"]:
                raise ValueError("标注来源与清单不符")
            self.documents.append(data); self.digests.append(hashlib.sha256(raw).hexdigest())
        self.history = []

    def save(self, video):
        path = Path(self.entries[video]["annotations"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.digests[video]:
            raise ValueError("标注文件被另一个程序修改，已停止覆盖；请关闭本窗口后重新打开")
        raw = json.dumps(self.documents[video], ensure_ascii=False, indent=2).encode("utf-8")
        tmp = path.with_name(path.name + ".review-tmp")
        tmp.write_bytes(raw); tmp.replace(path)
        self.digests[video] = hashlib.sha256(raw).hexdigest()

    def change(self, video, page, action):
        """A failed disk save must not leave an apparently successful in-memory edit."""
        before = copy.deepcopy(self.documents[video]["frames"][page])
        try:
            action(self.documents[video]["frames"][page])
            self.save(video)
        except Exception:
            self.documents[video]["frames"][page] = before
            raise
        self.history.append((video, page, before))
        self.history = self.history[-40:]

    def confirm(self, video, page, reviewer, *, selected=None, rank=None):
        if not reviewer.strip():
            raise ValueError("请填写一次复核人姓名")
        f = self.documents[video]["frames"][page]
        objects = f.get("objects", [])
        chosen = objects if selected is None else [objects[selected]]
        if rank is not None and rank not in LABELS:
            raise ValueError("无效点数")
        if any((rank if selected is not None and rank is not None else obj.get("rank")) not in LABELS for obj in chosen):
            raise ValueError("有未填写点数的框")
        now = datetime.now(timezone.utc).isoformat()
        def update(record):
            for obj in chosen:
                obj.setdefault("proposed_rank", obj["rank"])
                obj.setdefault("proposal_provenance", obj.get("label_provenance", "unspecified"))
                if rank is not None and selected is not None:
                    obj["rank"] = rank
                obj.update(label_provenance="human_reviewed", reviewed_by=reviewer.strip(), reviewed_at=now)
            record["complete"] = selected is None
            if selected is None:
                record.update(reviewed_by=reviewer.strip(), reviewed_at=now)
            else:
                record.pop("reviewed_at", None); record.pop("reviewed_by", None)
        self.change(video, page, update)

    def delete(self, video, page, selected):
        def update(f):
            f["objects"].pop(selected); f["complete"] = False
            f.pop("reviewed_at", None); f.pop("reviewed_by", None)
        self.change(video, page, update)

    def undo(self):
        if not self.history:
            return None
        video, page, record = self.history[-1]
        current = self.documents[video]["frames"][page]
        self.documents[video]["frames"][page] = record
        try:
            self.save(video)
        except Exception:
            self.documents[video]["frames"][page] = current
            raise
        self.history.pop()
        return video, page


def run_ui(bundle):
    import tkinter as tk
    from tkinter import ttk, messagebox
    from PIL import Image, ImageDraw, ImageTk

    store = ReviewStore(bundle)
    root = tk.Tk(); root.title("哈基米 · AI 预标注检查（改错即可，无需逐张画框）")
    root.geometry("1420x900")
    state = {"video": 0, "page": 0, "selected": None, "photo": None, "crop_photo": None}
    reviewer = tk.StringVar(value=store.bundle.get("default_reviewer", ""))
    chosen_video = tk.StringVar(); status = tk.StringVar(); detail = tk.StringVar()
    new_rank = tk.StringVar(value="unreadable"); drawing = tk.BooleanVar(value=False)
    show_junk = tk.BooleanVar(value=False)
    top = ttk.Frame(root, padding=8); top.pack(fill="x")
    titles = [f"{i+1}. {e['title']}" for i,e in enumerate(store.entries)]
    video_box = ttk.Combobox(top, values=titles, textvariable=chosen_video, width=26, state="readonly")
    video_box.pack(side="left"); ttk.Label(top, text="复核人").pack(side="left", padx=(14,4))
    ttk.Entry(top, textvariable=reviewer, width=16).pack(side="left")
    ttk.Label(top, text="蓝色待核 · 绿色已核 · 灰色非牌；编号自动生成").pack(side="left", padx=14)
    ttk.Checkbutton(top, text="显示非牌框", variable=show_junk, command=lambda: safe(load_page)).pack(side="left")
    ttk.Label(root, textvariable=status, padding=5).pack(fill="x")
    ttk.Label(root, text="看整图与预填标签；点框或列表查看放大图。按 2–9 / A J Q K / 0=10 / N=非牌 / U=看不清 即可改错；Enter 确认下一项，Ctrl+Enter 整页通过。",
              padding=5).pack(fill="x")
    canvas = tk.Canvas(root, background="#172033", highlightthickness=0); canvas.pack(fill="x", padx=10)
    lower = ttk.Frame(root, padding=8); lower.pack(fill="both", expand=True)
    table_frame = ttk.Frame(lower); table_frame.pack(side="left", fill="both", expand=True)
    tree = ttk.Treeview(table_frame, columns=("number", "rank", "status", "match"), show="headings", height=10)
    for key,title,width in (("number","编号",60),("rank","预填 / 已改点数",150),("status","复核状态",190),("match","原模型匹配度（非正确率）",170)):
        tree.heading(key,text=title); tree.column(key,width=width,anchor="center")
    scroll=ttk.Scrollbar(table_frame,command=tree.yview); tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right",fill="y"); tree.pack(fill="both",expand=True)
    editor=ttk.Frame(lower,padding=(15,0)); editor.pack(side="left",fill="y")
    crop_label=ttk.Label(editor); crop_label.pack()
    ttk.Label(editor,textvariable=detail,wraplength=350).pack(pady=5)
    buttons=ttk.Frame(editor); buttons.pack()
    bottom=ttk.Frame(root,padding=8); bottom.pack(fill="x")

    def frame(): return store.documents[state["video"]]["frames"][state["page"]]

    def safe(action):
        try: action()
        except Exception as exc: messagebox.showerror("未保存更改",str(exc),parent=root)

    def load_page(selected=None):
        entry=store.entries[state["video"]]; f=frame()
        bgr,digest=read_frame(entry["session"],f["file"])
        if digest!=f["sha256"]: raise ValueError("原帧内容已变化")
        state["image"]=Image.fromarray(bgr[:,:,::-1]); scale=min(1.0,1380/bgr.shape[1],390/bgr.shape[0])
        state["scale"]=scale; im=state["image"].resize((round(bgr.shape[1]*scale),round(bgr.shape[0]*scale)))
        state["photo"]=ImageTk.PhotoImage(im); canvas.configure(width=im.width,height=im.height)
        canvas.delete("all");canvas.create_image(0,0,anchor="nw",image=state["photo"])
        tree.delete(*tree.get_children());objects=f.get("objects",[])
        visible = [i for i,o in enumerate(objects) if show_junk.get() or o['rank'] != 'junk']
        state['visible'] = visible
        for i,o in enumerate(objects):
            if i not in visible: continue
            human=o.get("label_provenance")=="human_reviewed"; color="#49e6a2" if human else "#9ba5b7" if o['rank']=='junk' else "#65bbff"
            x,y,w,h=o['bbox'];canvas.create_rectangle(x*scale,y*scale,(x+w)*scale,(y+h)*scale,outline=color,width=1)
            canvas.create_text(x*scale,max(8,y*scale-7),text=f"{i+1}:{o['rank']}",fill=color,anchor="w",font=("Segoe UI",9,"bold"))
            score=o.get("proposal_score")
            tree.insert("","end",iid=str(i),values=(i+1,'非牌' if o['rank']=='junk' else '待辨认' if o['rank']=='unreadable' else o['rank'],
                '你已确认' if human else '有疑点，请优先看' if o.get('needs_attention') else 'AI 已检查，待你确认' if o.get('assistant_visual_reviewed') else '自动预标注，待确认',f"{score:.3f}" if score is not None else '—'))
        pages=store.documents[state['video']]['frames'];done=sum(f.get('complete') is True for f in pages)
        total=sum(len(d['frames']) for d in store.documents);all_done=sum(f.get('complete') is True for d in store.documents for f in d['frames'])
        elapsed=f.get('elapsed_s');stamp=f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}" if elapsed is not None else '未知时间'
        status.set(f"录像 {entry['title']} · {stamp} · 第 {state['page']+1}/{len(pages)} 页 · 全部已核 {all_done}/{total} 页 · 显示 {len(visible)} 框，非牌 {sum(o['rank']=='junk' for o in objects)} 框 · {'已完成' if f.get('complete') else '待你检查'}")
        chosen_video.set(titles[state['video']]); state['selected']=None;crop_label.configure(image='');detail.set('点选一个框可放大、改点数或删除。一个物理牌可能有多个角标；这一步先核对可见字形。')
        if visible:
            n=next((i for i in visible if i >= (selected or 0)),visible[-1]);tree.selection_set(str(n));tree.focus(str(n));tree.see(str(n));select()

    def select(_event=None):
        values=tree.selection()
        if not values:return
        n=int(values[0]);state['selected']=n;o=frame()['objects'][n];new_rank.set(o['rank']);x,y,w,h=o['bbox'];im=state['image'];pad=65
        crop=im.crop((max(0,x-pad),max(0,y-pad),min(im.width,x+w+pad),min(im.height,y+h+pad)))
        cx,cy=min(x,pad),min(y,pad)
        ImageDraw.Draw(crop).rectangle((cx,cy,cx+w,cy+h),outline='#13a5ff',width=2)
        scale=min(330/crop.width,175/crop.height);crop=crop.resize((max(1,round(crop.width*scale)),max(1,round(crop.height*scale))))
        state['crop_photo']=ImageTk.PhotoImage(crop);crop_label.configure(image=state['crop_photo'])
        before=o.get('proposal_before_assistant',o.get('proposed_rank',o['rank']))
        detail.set(f"编号 {n+1} · 当前 {o['rank']} · 原建议 {before}\n{o.get('review_hint', '点下面按钮或键盘改错；Enter 接受当前值。')}")
        canvas.delete('selected');s=state['scale'];canvas.create_rectangle(x*s,y*s,(x+w)*s,(y+h)*s,outline='#ffe07b',width=3,tags='selected')

    def set_rank(value=None):
        n=state['selected']
        if n is None:return
        store.confirm(state['video'],state['page'],reviewer.get(),selected=n,rank=value)
        load_page(min(n+1,len(frame()['objects'])-1));tree.focus_set()

    def move_page(delta=1, pending=False):
        locations=[(v,p) for v,d in enumerate(store.documents) for p,_ in enumerate(d['frames'])]
        start=locations.index((state['video'],state['page']))
        if pending:
            candidates=locations[start+1:]+locations[:start+1]
            target=next(((v,p) for v,p in candidates if not store.documents[v]['frames'][p].get('complete')),locations[start])
        else:target=locations[min(len(locations)-1,max(0,start+delta))]
        state['video'],state['page']=target;load_page()

    def complete_page():
        store.confirm(state['video'],state['page'],reviewer.get())
        move_page(pending=True)

    def remove():
        n=state['selected']
        if n is not None:store.delete(state['video'],state['page'],n);load_page(n)

    def undo():
        result=store.undo()
        if result:state['video'],state['page']=result;load_page()

    def switch(_event=None):
        state['video']=video_box.current(); pages=store.documents[state['video']]['frames']
        state['page']=next((i for i,f in enumerate(pages) if not f.get('complete')),0);load_page()

    for i,label in enumerate(LABELS):
        ttk.Button(buttons,text='非牌' if label=='junk' else '看不清' if label=='unreadable' else label,width=7,
                   command=lambda value=label:safe(lambda:set_rank(value))).grid(row=i//5,column=i%5,padx=2,pady=3)
    for title,command in (("上一页",lambda:move_page(-1)),("下一页",lambda:move_page(1)),("下一待核页",lambda:move_page(pending=True)),
                          ("确认此项 ↵",set_rank),("删除误框 Del",remove),("撤销 Ctrl+Z",undo),("整页通过（含非牌）Ctrl+Enter",complete_page)):
        ttk.Button(bottom,text=title,command=lambda fn=command:safe(fn)).pack(side='left',padx=3)
    ttk.Checkbutton(bottom,text="补漏框",variable=drawing).pack(side='left',padx=5)

    def press(event):
        if drawing.get():state['start']=(event.x,event.y);return
        x,y=event.x/state['scale'],event.y/state['scale'];choices=[]
        for i,o in enumerate(frame()['objects']):
            if i not in state['visible']: continue
            bx,by,bw,bh=o['bbox']
            if bx<=x<=bx+bw and by<=y<=by+bh:choices.append(((x-bx-bw/2)**2+(y-by-bh/2)**2,i))
        if choices:
            n=min(choices)[1];tree.selection_set(str(n));tree.see(str(n));select();tree.focus_set()

    def drag(event):
        if 'start' in state:canvas.delete('drag');canvas.create_rectangle(*state['start'],event.x,event.y,outline='cyan',tags='drag')

    def release(event):
        if 'start' not in state:return
        sx,sy=state.pop('start');s=state['scale'];x,y=round(min(sx,event.x)/s),round(min(sy,event.y)/s);w,h=round(abs(sx-event.x)/s),round(abs(sy-event.y)/s)
        if min(w,h)<3:return
        im=state['image'];x=max(0,x);y=max(0,y);w=min(w,im.width-x);h=min(h,im.height-y)
        if min(w,h)<=0:return
        f=frame();cid=hashlib.sha256(f"{f['file']}:{x,y,w,h}:{len(f['objects'])}".encode()).hexdigest()[:16]
        def append(record):
            record['objects'].append({'bbox':[x,y,w,h],'rank':'unreadable','physical_card_id':'candidate-'+cid,'identity_provenance':'automatic_candidate_not_verified_physical_card',
                'region_id':'unassigned','label_provenance':'unspecified','rejection_reason':'用户补框，待选择点数'})
            record['complete']=False
            record.pop('reviewed_at',None);record.pop('reviewed_by',None)
        store.change(state['video'],state['page'],append);drawing.set(False);load_page(len(f['objects'])-1);tree.focus_set()

    def key(event):
        if isinstance(root.focus_get(),(tk.Entry,ttk.Entry,ttk.Combobox)):return
        char=(event.char or '').lower();name=event.keysym.lower()
        if event.state & 4 and name in ('return','kp_enter'):safe(complete_page);return 'break'
        if event.state & 4 and name=='z':safe(undo);return 'break'
        if name in ('return','space'):safe(set_rank);return 'break'
        if name=='delete':safe(remove);return 'break'
        value={'0':'10','a':'A','j':'J','q':'Q','k':'K','n':'junk','u':'unreadable'}.get(char,char if char in '23456789' and char else None)
        if value:safe(lambda:set_rank(value));return 'break'
    tree.bind('<<TreeviewSelect>>',select);video_box.bind('<<ComboboxSelected>>',switch)
    canvas.bind('<ButtonPress-1>',press);canvas.bind('<B1-Motion>',drag);canvas.bind('<ButtonRelease-1>',lambda e:safe(lambda:release(e)))
    root.bind('<Key>',key)
    state['page']=next((i for i,f in enumerate(store.documents[0]['frames']) if not f.get('complete')),0)
    load_page();root.mainloop()


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle');run_ui(p.parse_args().bundle)
