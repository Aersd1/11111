"""Continuous section reading and persistent draggable document annotations."""
import html
import json
from pathlib import Path
from PySide6.QtCore import QObject, Slot, QFile, QIODevice, QTimer, Qt, QEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import QDialog, QVBoxLayout, QScrollArea
from markdown_ui import MarkdownPreview, markdown_html, zoom_toolbar


def paper_sections(paper):
    fields = [('研究总结', 'summary'), ('个人描述', 'description'), ('摘要', 'abstract'),
              ('引言', 'introduction'), ('结论', 'conclusion'), ('局限', 'limitations'),
              ('关键词', 'keywords'), ('整理依据', 'basis'), ('处理状态', 'status'), ('原文件', 'path')]
    return [(title, paper.get(key) or '尚未填写') for title, key in fields]


class SectionPreview(MarkdownPreview):
    def __init__(self, sections, base_dir=None, expand_height=False):
        self.expand_height = expand_height
        super().__init__('', base_dir=base_dir)
        blocks = []
        for title, text in sections:
            import re
            markup = markdown_html(text, base_dir)
            match = re.search(r'<body[^>]*>([\s\S]*)</body>', markup)
            blocks.append('<details open><summary>' + html.escape(title) + '</summary>' + (match[1] if match else markup) + '</details>')
        self.setHtml('<style>summary{cursor:pointer;font-size:17px;font-weight:650;color:#574184;padding:12px 0}details{border-bottom:1px solid #eee;padding-bottom:8px}</style>' + ''.join(blocks))
        if expand_height:
            self.height_timer = QTimer(self)
            self.height_timer.timeout.connect(self.fit_content)
            self.height_timer.start(350)

    def eventFilter(self, watched, event):
        if self.expand_height and event.type() == QEvent.Type.Wheel and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            parent = self.parentWidget()
            while parent and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent:
                bar = parent.verticalScrollBar()
                delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * bar.singleStep() * 3
                bar.setValue(bar.value() - round(delta))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def fit_content(self):
        if not self._ready:
            return
        def resize(value):
            from shiboken6 import isValid
            if isValid(self) and isinstance(value, (int, float)):
                height = max(80, int((value + 40) * self.zoomFactor()))
                if abs(self.height() - height) > 2:
                    self.setFixedHeight(height)
        self.page().runJavaScript('document.getElementById("content").getBoundingClientRect().height', resize)


class PaperPreviewWindow(QDialog):
    def __init__(self, paper, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowTitle('阅读预览 · ' + paper['title'])
        layout = QVBoxLayout(self)
        preview = SectionPreview(paper_sections(paper), Path(paper['path']).parent)
        layout.addWidget(zoom_toolbar(preview))
        layout.addWidget(preview, 1)
        self.resize(1100, 850)


class NoteBridge(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    @Slot(str)
    def save(self, encoded):
        try:
            notes = json.loads(encoded)
            if isinstance(notes, dict):
                page, items = notes.get('page'), notes.get('notes')
                if type(page) is not int or not isinstance(items, list):
                    return
                notes = [n for n in self.owner.notes if n.get('page', 1) != page] + [dict(n, page=page) for n in items]
            if not isinstance(notes, list):
                return
            self.owner.notes = notes
            self.owner.changed()
        except (TypeError, ValueError):
            return


NOTE_SCRIPT = r'''
window.noteItems=[];
window.saveNotes=function(){if(window.noteBridge)noteBridge.save(JSON.stringify(window.notePage == null ? noteItems : {page:window.notePage,notes:noteItems}));};
window.drawNotes=function(items){
  document.querySelectorAll('.paper-note').forEach(e=>e.remove());noteItems=items;
  items.forEach(n=>{
    const box=document.createElement('aside');box.className='paper-note';
    box.style.cssText='position:absolute;width:190px;z-index:20;background:#fff2aa;border:1px solid #dcc56b;box-shadow:0 3px 12px #0002;border-radius:6px;color:#443b20';
    const place=()=>{box.style.left=Math.max(0,Math.min(n.x,Math.max(0,innerWidth-205)))+'px';box.style.top=Math.max(0,n.y)+'px';};place();
    const handle=document.createElement('div');handle.style.cssText='cursor:move;padding:4px 8px;font-size:12px;touch-action:none';handle.textContent='便签 · 拖动移动';
    const remove=document.createElement('button');remove.textContent='×';remove.title='删除便签';remove.style.cssText='float:right;cursor:pointer';
    remove.onclick=()=>{noteItems=noteItems.filter(x=>x.id!==n.id);box.remove();saveNotes();};handle.append(remove);
    const text=document.createElement('textarea');text.value=n.text;text.placeholder='写下批注…';text.style.cssText='box-sizing:border-box;width:100%;height:95px;border:0;padding:8px;background:transparent;resize:vertical;font:14px/1.5 sans-serif';
    text.oninput=()=>{n.text=text.value;saveNotes();};
    let drag=null;handle.onpointerdown=e=>{if(e.target===remove)return;drag={x:e.clientX,y:e.clientY,left:parseFloat(box.style.left),top:n.y};handle.setPointerCapture(e.pointerId);};
    handle.onpointermove=e=>{if(!drag)return;n.x=Math.max(0,Math.min(innerWidth-205,drag.left+e.clientX-drag.x));n.y=Math.max(0,drag.top+e.clientY-drag.y);place();};
    handle.onpointerup=e=>{if(drag){drag=null;saveNotes();}};
    box.append(handle,text);document.body.append(box);
  });
};
window.addPaperNote=function(){const n={id:String(Date.now())+Math.random(),x:Math.max(0,innerWidth-220-(noteItems.length%5)*20),y:scrollY+60+(noteItems.length%5)*24,text:''};drawNotes([...noteItems,n]);saveNotes();};
new QWebChannel(qt.webChannelTransport,function(channel){window.noteBridge=channel.objects.notes;drawNotes(window.initialNotes||[]);});
'''


class AnnotatedPreview(MarkdownPreview):
    def __init__(self, side, changed):
        super().__init__()
        self.note_side, self.changed, self.notes = side, changed, []
        self.note_page = None
        self.boundary_scroll = None
        self._last_page_wheel = 0
        self.channel = QWebChannel(self.page())
        self.bridge = NoteBridge(self)
        self.channel.registerObject('notes', self.bridge)
        self.page().setWebChannel(self.channel)
        self.loadFinished.connect(self.setup_notes)

    def setup_notes(self, ok):
        if not ok:
            return
        source = QFile(':/qtwebchannel/qwebchannel.js')
        if source.open(QIODevice.OpenModeFlag.ReadOnly):
            script = bytes(source.readAll()).decode('utf-8')
            source.close()
            self.page().runJavaScript(script + '\nwindow.initialNotes=' + json.dumps(self.visible_notes()) + ';window.notePage=' + json.dumps(self.note_page) + ';\n' + NOTE_SCRIPT)

    def restore_notes(self, notes):
        self.notes = notes
        if self._ready:
            self.page().runJavaScript('window.initialNotes=' + json.dumps(self.visible_notes()) + ';window.notePage=' + json.dumps(self.note_page) + ';if(window.drawNotes)drawNotes(initialNotes);')

    def flush_notes(self, done):
        if not self._ready:
            done()
            return
        def received(value):
            from shiboken6 import isValid
            if not isValid(self):
                return
            if isinstance(value, str):
                self.bridge.save(value)
            done()
        self.page().runJavaScript('window.noteItems ? JSON.stringify(window.notePage == null ? noteItems : {page:window.notePage,notes:noteItems}) : null', received)

    def eventFilter(self, watched, event):
        if self.boundary_scroll and event.type() == QEvent.Type.Wheel and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            import time
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta and time.monotonic() - self._last_page_wheel > .5:
                scope = self.note_page
                direction = 1 if delta < 0 else -1
                def check(at_edge):
                    from shiboken6 import isValid
                    if isValid(self) and at_edge and scope == self.note_page and time.monotonic()-self._last_page_wheel > .5:
                        self._last_page_wheel = time.monotonic()
                        self.boundary_scroll(direction)
                self.page().runJavaScript('scrollY + innerHeight >= document.documentElement.scrollHeight - 3' if direction > 0 else 'scrollY <= 1', check)
        return super().eventFilter(watched, event)

    def visible_notes(self):
        return self.notes if self.note_page is None else [n for n in self.notes if n.get('page', 1) == self.note_page]

    def set_note_page(self, page):
        if page != self.note_page:
            self.note_page = page
            self.restore_notes(self.notes)

    def add_note(self):
        if self._ready:
            self.page().runJavaScript('if(window.addPaperNote)addPaperNote();')
