from collections import defaultdict
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

def _me(data):
    m=data.get("me",{}); return m.get("Me",m) if isinstance(m,dict) else {}

def _student_name(data):
    m=_me(data)
    return " ".join(filter(None,[m.get("FirstName"),m.get("LastName")])) or str(m.get("Login") or "Librus")

def _ident(data, fallback):
    m=_me(data); return str(m.get("AccountId") or m.get("Id") or fallback)

async def async_setup_entry(hass, entry, async_add_entities):
    c=hass.data[DOMAIN][entry.entry_id]
    entities=[GradesEntity(c,entry), AttendanceEntity(c,entry), HomeworkEntity(c,entry)]
    subjects=sorted({g.get("subject_name") for g in c.data.get("grades",[]) if g.get("subject_name")})
    entities += [SubjectGradesEntity(c,entry,s) for s in subjects]
    async_add_entities(entities)

class Base(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name=True
    def __init__(self,c,e): super().__init__(c); self.entry=e
    @property
    def device_info(self):
        ident=_ident(self.coordinator.data,self.entry.entry_id)
        return DeviceInfo(identifiers={(DOMAIN,ident)},name=f"Librus – {_student_name(self.coordinator.data)}",manufacturer="Librus (unofficial)",model="Synergia")

class GradesEntity(Base):
    _attr_name="Wszystkie oceny"
    @property
    def unique_id(self): return f"{_ident(self.coordinator.data,self.entry.entry_id)}_grades"
    @property
    def native_value(self): return len(self.coordinator.data.get("grades",[]))
    @property
    def extra_state_attributes(self):
        grades=self.coordinator.data.get("grades",[])
        return {"student":_student_name(self.coordinator.data),"grades":grades[-150:],"values":[g.get("display_value") for g in grades]}

class SubjectGradesEntity(Base):
    def __init__(self,c,e,subject): super().__init__(c,e); self.subject=subject; self._attr_name=f"Oceny – {subject}"
    @property
    def unique_id(self): return f"{_ident(self.coordinator.data,self.entry.entry_id)}_subject_{self.subject}"
    def rows(self): return [g for g in self.coordinator.data.get("grades",[]) if g.get("subject_name")==self.subject]
    @property
    def native_value(self): return ", ".join(g.get("display_value","") for g in self.rows()) or "brak"
    @property
    def extra_state_attributes(self): return {"subject":self.subject,"grades":self.rows()[-100:]}

class AttendanceEntity(Base):
    _attr_name="Frekwencja – rekordy"
    @property
    def unique_id(self): return f"{_ident(self.coordinator.data,self.entry.entry_id)}_attendance"
    @property
    def native_value(self):
        d=self.coordinator.data.get("attendances_raw",{}); rows=d.get("Attendances",[]) if isinstance(d,dict) else []
        return len(rows)
    @property
    def extra_state_attributes(self): return {"raw":self.coordinator.data.get("attendances_raw",{})}

class HomeworkEntity(Base):
    _attr_name="Zadania domowe"
    @property
    def unique_id(self): return f"{_ident(self.coordinator.data,self.entry.entry_id)}_homework"
    @property
    def native_value(self):
        d=self.coordinator.data.get("homework_raw",{}); rows=(d.get("HomeWorkAssignments") or d.get("Homework") or []) if isinstance(d,dict) else []
        return len(rows)
    @property
    def extra_state_attributes(self): return {"raw":self.coordinator.data.get("homework_raw",{})}
