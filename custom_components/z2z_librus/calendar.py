from datetime import datetime, timedelta
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    c=hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AgendaCalendar(c,entry)])

class AgendaCalendar(CoordinatorEntity, CalendarEntity):
    _attr_name="Librus – terminarz"
    _attr_has_entity_name=False
    def __init__(self,c,e): super().__init__(c); self.entry=e; self._attr_unique_id=f"{e.entry_id}_agenda"
    @property
    def event(self):
        now=datetime.now().astimezone(); events=self._events(now,now+timedelta(days=90)); return events[0] if events else None
    async def async_get_events(self,hass,start_date,end_date): return self._events(start_date,end_date)
    def _events(self,start,end):
        # Homework is reliable API data and is exposed as calendar events immediately.
        d=self.coordinator.data.get("homework_raw",{}); rows=[]
        if isinstance(d,dict): rows=d.get("HomeWorkAssignments") or d.get("Homework") or []
        out=[]
        for x in rows:
            raw=x.get("Date") or x.get("DueDate") or x.get("Deadline")
            if not raw: continue
            try:
                dt=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
                if dt.tzinfo is None: dt=dt.astimezone()
            except Exception: continue
            if start <= dt <= end:
                out.append(CalendarEvent(summary=x.get("Topic") or x.get("Content") or "Zadanie Librus",start=dt,end=dt+timedelta(minutes=30),description=str(x)))
        return sorted(out,key=lambda e:e.start)
