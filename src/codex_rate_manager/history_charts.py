from __future__ import annotations
import bisect
from datetime import datetime
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QVBoxLayout, QWidget

def series_stats(rates, key):
    values=[float(r[key]) for r in rates if r.get(key) is not None]
    return {"start": values[0] if values else None, "latest": values[-1] if values else None, "minimum": min(values) if values else None, "maximum": max(values) if values else None, "decline": sum(max(a-b,0.0) for a,b in zip(values,values[1:]))}

def reduce_points(points, limit=1200, required=()):
    if len(points)<=limit: return list(points)
    indexes={0,len(points)-1}
    for t in required: indexes.add(min(range(len(points)),key=lambda i:abs(float(points[i].get("timestamp",0))-float(t))))
    for key in ("five_remaining","weekly_remaining"):
        valid=[i for i,p in enumerate(points) if p.get(key) is not None]
        if valid: indexes.update((min(valid,key=lambda i:float(points[i][key])),max(valid,key=lambda i:float(points[i][key]))))
    step=max(1,len(points)//max(1,limit-len(indexes))); indexes.update(range(0,len(points),step))
    return [points[i] for i in sorted(indexes)[:limit]]

class _SeriesWidget(QWidget):
    def __init__(self,key,title,color,parent=None):
        super().__init__(parent); self.key,self.title,self.color=key,title,color; self.rates=[]; self.recoveries=[]; self.hover_rate=None; self.hover_timestamp=None; self.setMouseTracking(True); self.setMinimumHeight(180)
    def set_data(self,rates,recoveries): self.rates,self.recoveries=rates or [],recoveries or []; self.update()
    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),self.palette().window()); plot=self.rect().adjusted(52,22,-14,-26); p.setPen(self.palette().mid().color())
        for i in range(6):
            y=plot.bottom()-plot.height()*i/5; p.drawLine(plot.left(),int(y),plot.right(),int(y)); p.drawText(4,int(y)+4,f"{i*20}%")
        p.setPen(self.palette().text().color()); p.drawText(plot.left(),15,self.title); valid=[(float(r["timestamp"]),float(r[self.key])) for r in self.rates if r.get(self.key) is not None]
        if not valid: p.drawText(plot,Qt.AlignmentFlag.AlignCenter,"表示できる履歴データがありません"); return
        lo=valid[0][0]; hi=valid[-1][0] if valid[-1][0]!=lo else lo+1
        def xy(t,v): return plot.left()+(t-lo)/(hi-lo)*plot.width(),plot.bottom()-max(0,min(100,v))/100*plot.height()
        p.setPen(QPen(self.color,2)); pts=[xy(t,v) for t,v in valid]
        for a,b in zip(pts,pts[1:]): p.drawLine(int(a[0]),int(a[1]),int(b[0]),int(b[1]))
        p.setPen(QPen(QColor("#ef5350"),2)); event_type="RATE_5H_RECOVERED" if self.key.startswith("five") else "RATE_WEEKLY_RECOVERED"
        for r in self.recoveries:
            if r.get("event_type")==event_type:
                x,y=xy(float(r["timestamp"]),float(r.get("current_remaining") or 0)); p.drawEllipse(int(x)-4,int(y)-4,8,8)
        if self.hover_rate is not None:
            x,y=xy(self.hover_timestamp, float(self.hover_rate[self.key])) if self.hover_rate.get(self.key) is not None else (None,None)
            pen=QPen(self.palette().mid().color(), 1, Qt.PenStyle.DashLine); p.setPen(pen); p.drawLine(int(xy(self.hover_timestamp, 0)[0]), plot.top(), int(xy(self.hover_timestamp, 0)[0]), plot.bottom())
            if x is not None:
                p.setPen(QPen(self.color, 2)); p.setBrush(self.palette().window()); p.drawEllipse(int(x)-5,int(y)-5,10,10)
        p.setPen(self.palette().text().color()); p.drawText(plot.left(),self.height()-7,datetime.fromtimestamp(lo).strftime("%m/%d %H:%M")); p.drawText(plot.right()-95,self.height()-7,datetime.fromtimestamp(hi).strftime("%m/%d %H:%M"))

    def _plot(self): return self.rect().adjusted(52,22,-14,-26)
    def timestamp_at_x(self, x):
        valid=[float(r["timestamp"]) for r in self.rates if r.get(self.key) is not None]
        if not valid: return None
        plot=self._plot(); lo,hi=valid[0],valid[-1] if valid[-1]!=valid[0] else valid[0]+1
        return lo + (max(plot.left(),min(plot.right(),x))-plot.left())/plot.width()*(hi-lo)
    def nearest_raw(self, timestamp, max_pixels=12):
        candidates=[r for r in self.rates if r.get(self.key) is not None]
        if not candidates: return None
        times=[float(r["timestamp"]) for r in candidates]; i=bisect.bisect_left(times,timestamp); choices=candidates[max(0,i-1):min(len(candidates),i+1)]
        chosen=min(choices,key=lambda r:abs(float(r["timestamp"])-timestamp))
        plot=self._plot(); span=times[-1]-times[0] or 1
        if abs(float(chosen["timestamp"])-timestamp)/span*plot.width() > max_pixels: return None
        return chosen
    def mouseMoveEvent(self,event):
        timestamp=self.timestamp_at_x(event.position().x()); rate=self.nearest_raw(timestamp) if timestamp is not None else None
        if rate is self.hover_rate: return
        self.hover_rate,self.hover_timestamp=rate,(float(rate["timestamp"]) if rate else None); self.update()
        if rate: QToolTip.showText(self.mapToGlobal(QPoint(int(event.position().x())+12,int(event.position().y())+12)), self._tooltip(rate), self)
        else: QToolTip.hideText()
    def leaveEvent(self,event):
        if self.hover_rate is not None: self.hover_rate=self.hover_timestamp=None; self.update(); QToolTip.hideText()
    def _tooltip(self,r):
        def f(v): return "—" if v is None else f"{float(v):.1f}%"
        def d(v): return "—" if v is None else datetime.fromtimestamp(float(v)).strftime("%Y/%m/%d %H:%M:%S")
        first="5時間" if self.key.startswith("five") else "週間"; other="週間" if first=="5時間" else "5時間"
        return (f"実ログ\n{d(r['timestamp'])}\n\n{first}レート\n残量: {f(r.get(first=='5時間' and 'five_remaining' or 'weekly_remaining'))}\n使用率: {f(r.get(first=='5時間' and 'five_used' or 'weekly_used'))}\n次回リセット: {d(r.get(first=='5時間' and 'five_reset' or 'weekly_reset'))}\n\n{other}レート\n残量: {f(r.get(other=='5時間' and 'five_remaining' or 'weekly_remaining'))}\n使用率: {f(r.get(other=='5時間' and 'five_used' or 'weekly_used'))}\n次回リセット: {d(r.get(other=='5時間' and 'five_reset' or 'weekly_reset'))}\n\n状態: {r.get('state') or '—'}")

class HistoryChart(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.data={"rates":[],"recoveries":[]}; self.five=_SeriesWidget("five_remaining","5時間レート残量",QColor("#4db6ac"),self); self.weekly=_SeriesWidget("weekly_remaining","週間レート残量",QColor("#ffb74d"),self); layout=QVBoxLayout(self); layout.addWidget(self.five); layout.addWidget(self.weekly); self.setMinimumHeight(420)
    def set_data(self,data):
        self.data=data or {"rates":[],"recoveries":[]}; self.five.set_data(self.data.get("rates"),self.data.get("recoveries")); self.weekly.set_data(self.data.get("rates"),self.data.get("recoveries"))
    def rendered_pixmap(self):
        pix=QPixmap(self.size()); pix.fill(self.palette().window().color()); self.render(pix); return pix
