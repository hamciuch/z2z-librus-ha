from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from aiohttp import ClientSession, ClientResponseError

from .const import BASE, API

class LibrusError(Exception): pass
class LibrusAuthError(LibrusError): pass

@dataclass
class LibrusClient:
    session: ClientSession
    username: str
    password: str

    async def login(self) -> None:
        # Current Synergia flow (changed in 2026): bootstrap portalRodzina first.
        async with self.session.get(f"{BASE}/loguj/portalRodzina", allow_redirects=True) as r:
            await r.text()
        # The login form ultimately posts login/passwd to Synergia. Keep redirects/cookies.
        payload = {"login": self.username, "passwd": self.password, "ed_pass_keydown": "", "cz": ""}
        async with self.session.post(f"{BASE}/loguj", data=payload, allow_redirects=True) as r:
            await r.text()
        try:
            await self.get("Me")
        except Exception as err:
            raise LibrusAuthError("Librus login failed or additional captcha/2FA is required") from err

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API}/{path.lstrip('/')}"
        async with self.session.get(url, params=params, headers={"Accept": "application/json"}) as r:
            if r.status in (401, 403):
                raise LibrusAuthError(f"HTTP {r.status}")
            r.raise_for_status()
            return await r.json(content_type=None)

    @staticmethod
    def resources(obj: Any, *keys: str) -> list[dict[str, Any]]:
        cur = obj
        for key in keys:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return []
        return cur if isinstance(cur, list) else []

    async def fetch_core(self) -> dict[str, Any]:
        me = await self.get("Me")
        grades = await self.get("Grades")
        out = {"me": me, "grades_raw": grades}
        # Optional endpoints differ by school/modules. Failure of one must not kill the integration.
        for name, endpoint in {
            "attendances_raw": "Attendances",
            "homework_raw": "HomeWorkAssignments",
            "notices_raw": "SchoolNotices",
            "grade_types_raw": "Grades/Types",
            "grade_comments_raw": "Grades/Comments",
        }.items():
            try: out[name] = await self.get(endpoint)
            except Exception: out[name] = {}
        return out

    async def resolve(self, path: str) -> dict[str, Any]:
        try:
            data = await self.get(path)
            if isinstance(data, dict):
                for key in ("Subject", "Category", "User", "Lesson"):
                    if isinstance(data.get(key), dict): return data[key]
                return data
        except Exception:
            pass
        return {}

    async def enrich_grades(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        grades = self.resources(raw, "Grades") or self.resources(raw, "grades")
        result=[]; subjects={}; categories={}; users={}
        for g in grades:
            x=dict(g)
            sid=((g.get("Subject") or {}).get("Id") if isinstance(g.get("Subject"),dict) else g.get("Subject"))
            cid=((g.get("Category") or {}).get("Id") if isinstance(g.get("Category"),dict) else g.get("Category"))
            uid=((g.get("AddedBy") or {}).get("Id") if isinstance(g.get("AddedBy"),dict) else g.get("AddedBy"))
            if sid:
                if sid not in subjects: subjects[sid]=await self.resolve(f"Subjects/{sid}")
                x["subject_name"]=subjects[sid].get("Name") or subjects[sid].get("name") or str(sid)
            if cid:
                if cid not in categories: categories[cid]=await self.resolve(f"Grades/Categories/{cid}")
                x["category_name"]=categories[cid].get("Name") or categories[cid].get("name") or str(cid)
            if uid:
                if uid not in users: users[uid]=await self.resolve(f"Users/{uid}")
                u=users[uid]; x["teacher"]=" ".join(filter(None,[u.get("FirstName"),u.get("LastName")]))
            # Preserve Librus value EXACTLY: 5+, 4-, +, -, bz, np etc.
            x["display_value"] = str(g.get("Grade") or g.get("Value") or g.get("grade") or "")
            result.append(x)
        return result
