"""Mały lokalny panel PEva oparty wyłącznie na standardowej bibliotece Pythona.

Serwer nasłuchuje wyłącznie na ``127.0.0.1``.  Nie ma logowania, dostępu z
sieci ani endpointu przyjmującego tekst do wysłania.  Może jedynie włączyć
wąski kanał odpowiedzi zatwierdzonym aliasom, którego tekst i cel weryfikują
runtime oraz Oczy.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peva_runtime import PEvaRuntime


class LocalPanel:
    """Panel stanu, który można osadzić w normalnym starcie PEva."""

    def __init__(self, runtime: "PEvaRuntime", *, host: str = "127.0.0.1", port: int = 8766) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("LocalPanel may listen only on the local computer.")
        self.runtime = runtime
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str | None:
        server = self._server
        if server is None:
            return None
        return f"http://127.0.0.1:{server.server_port}/"

    def start(self) -> str:
        if self._server is not None:
            assert self.url is not None
            return self.url
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/api/status":
                    self._json(panel.runtime.status_snapshot())
                    return
                if self.path == "/api/preview":
                    self._json(panel.runtime.preview_snapshot())
                    return
                if self.path == "/api/friends":
                    self._json({"candidates": panel.runtime.friend_proposals_snapshot()})
                    return
                if self.path == "/":
                    self._html(_render_page(panel.runtime.status_snapshot()))
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.startswith("/api/friends/"):
                    self._friend_action()
                    return
                if self.path == "/api/safe":
                    panel.runtime.stop_actions("Stopped from local panel")
                elif self.path == "/api/resume":
                    panel.runtime.resume_actions("Resumed from local panel")
                elif self.path == "/api/eyes/start":
                    panel.runtime.start_eyes()
                elif self.path == "/api/eyes/stop":
                    panel.runtime.stop_eyes()
                elif self.path == "/api/model/start":
                    panel.runtime.start_model()
                elif self.path == "/api/model/stop":
                    panel.runtime.stop_model()
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._json(panel.runtime.status_snapshot())

            def _friend_action(self) -> None:
                parts = self.path.split("/")
                if len(parts) != 5 or not parts[3] or not parts[4]:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 1024:
                        raise ValueError("invalid request size")
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("invalid request body")
                    alias = payload.get("alias", "")
                    if not isinstance(alias, str):
                        raise ValueError("invalid alias")
                    if parts[3] == "new" and parts[4] == "register":
                        updated = panel.runtime.register_confirmed_friend(alias=alias)
                    else:
                        updated = panel.runtime.update_friend_proposal(parts[3], parts[4], alias=alias)
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._json(updated.public_dict())

            def _json(self, payload: object) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _html(self, page: str) -> None:
                data = page.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                # The panel must not write browser noise or chat details to a console.
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="PEvaPanel", daemon=True)
        self._thread.start()
        assert self.url is not None
        return self.url

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


def _render_page(snapshot: dict[str, object]) -> str:
    """Renderuj lokalny ekran bez przekazywania tekstu rozmów do przeglądarki."""
    safe = "TAK" if snapshot.get("safe") else "NIE"
    model = snapshot.get("model") if isinstance(snapshot.get("model"), dict) else {}
    model_ready = "gotowy" if model.get("ready") else "niegotowy"
    decision = snapshot.get("last_decision")
    decision_text = "brak" if not isinstance(decision, dict) else str(decision.get("kind", "brak"))
    flow = snapshot.get("conversation_flow") if isinstance(snapshot.get("conversation_flow"), dict) else {}
    flow_text = _flow_label(str(flow.get("phase", "IDLE")), str(flow.get("reason", "")))
    delivery = snapshot.get("automatic_delivery") if isinstance(snapshot.get("automatic_delivery"), dict) else {}
    delivery_enabled = bool(delivery.get("enabled"))
    approved_contacts = int(delivery.get("approved_contacts", 0) or 0)
    last_delivery_text = _delivery_label(
        str(delivery.get("last_state") or ""),
        str(delivery.get("last_reason") or ""),
    )
    delivery_text = (
        f"włączona dla {approved_contacts} zatwierdzonych osób"
        if delivery_enabled and approved_contacts
        else "zatrzymana" if not delivery_enabled else "włączona, ale bez zatwierdzonej osoby"
    )
    return f"""<!doctype html>
<html lang=\"pl\"><head><meta charset=\"utf-8\"><title>PEva</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#151515;color:#eee;max-width:720px;margin:42px auto;padding:0 20px}}section{{background:#202020;border:1px solid #343434;border-radius:12px;padding:18px;margin:14px 0}}button{{background:#0b5a91;color:white;border:0;border-radius:8px;padding:10px 14px;margin:4px;cursor:pointer}}button.danger{{background:#8b3030}}.safe{{color:#6ee7a0}}small{{color:#aaa}}#preview{{white-space:pre-wrap;margin:10px 0 0}}.candidate{{border-top:1px solid #3a3a3a;padding:13px 0}}.candidate:first-child{{border-top:0}}input{{background:#101010;color:#eee;border:1px solid #555;border-radius:6px;padding:8px;max-width:220px}}</style></head>
<body><h1>PEva</h1><section><b>SAFE:</b> <span class=\"safe\">{safe}</span><br>
<b>Model:</b> {model_ready}<br><b>Oczy:</b> {"działają" if snapshot.get("eyes_running") else "zatrzymane"}<br>
<b>Rozmowa:</b> {flow_text}<br><b>Ostatnia decyzja:</b> {decision_text}<br><b>Automatyczna odpowiedź:</b> {delivery_text}<br><b>Ostatnia próba:</b> {last_delivery_text}<br><small>PEva najpierw zbiera krótką serię wiadomości. Odpowiada wyłącznie w aktualnie otwartym PM do dokładnego aliasu zatwierdzonego przez Ciebie. Przed wpisaniem sprawdza nagłówek i puste pole wiadomości, a po wysłaniu szuka nowego własnego dymka. Nie wybiera osób z listy i nie pisze w World/Alliance.</small></section>
<section><b>Propozycja odpowiedzi PEvy</b><div id=\"preview\">Czekam na odpowiedź modelu…</div><small>Widoczne tylko lokalnie podczas tej sesji. Tekst rozmowy nie jest tu zapisywany.</small></section>
<section><b>Lista znajomych PEvy i propozycji</b><div id=\"friends\">Brak osób na liście.</div><small>To jest wyłącznie lokalna lista PEvy. Usunięcie nie zmieni niczego w Last Z.</small></section>
<section><b>Dodaj istniejącego znajomego</b><p><small>Najpierw ręcznie ustaw tej osobie unikalny alias na koncie PEva w Last Z. Ten alias jest stałym kluczem lokalnej listy PEvy.</small></p><label>Alias ustawiony w Last Z<br><input id=\"manualAlias\" placeholder=\"np. DOKTORB\" maxlength=\"32\"></label><button onclick=\"registerFriend()\">Zapisz istniejącego znajomego</button><div id=\"registrationStatus\"><small>Po zapisie osoba pojawi się powyżej na liście.</small></div></section>
<section><button onclick=\"post('/api/safe')\">Zatrzymaj automatyczne odpowiedzi (SAFE)</button><button onclick=\"post('/api/resume')\">Włącz odpowiedzi zatwierdzonym osobom</button><button onclick=\"post('/api/eyes/start')\">Włącz Oczy</button><button onclick=\"post('/api/eyes/stop')\">Wyłącz Oczy</button><button onclick=\"post('/api/model/start')\">Włącz model</button><button onclick=\"post('/api/model/stop')\">Wyłącz model</button></section>
<script>
async function post(p){{await fetch(p,{{method:'POST'}});location.reload()}}
async function preview(){{const r=await fetch('/api/preview');const p=await r.json();document.getElementById('preview').textContent=p.available?p.text:'Brak propozycji — PEva jeszcze nie odpowiedziała.'}}
async function registerFriend(){{const alias=document.getElementById('manualAlias').value;const status=document.getElementById('registrationStatus');const r=await fetch('/api/friends/new/register',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{alias:alias}})}});if(!r.ok){{status.textContent='Nie zapisano: wpisz alias, który został ręcznie ustawiony w Last Z.';return}}const friend=await r.json();document.getElementById('manualAlias').value='';status.textContent='Dodano do listy: '+friend.final_alias+'. Automatyczne odpowiedzi są nadal wyłączone.';await friends()}}
function add(parent, tag, text, className){{const e=document.createElement(tag);e.textContent=text;if(className)e.className=className;parent.appendChild(e);return e}}
function actionButton(parent,label,id,action,aliasInput,danger){{const b=document.createElement('button');b.textContent=label;if(danger)b.className='danger';b.onclick=async()=>{{if(action==='remove'&&!confirm('Usunąć tę osobę tylko z lokalnej listy PEvy? Last Z nie zostanie zmienione.'))return;const payload=aliasInput?{{alias:aliasInput.value}}:{{}};const r=await fetch('/api/friends/'+encodeURIComponent(id)+'/'+action,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});if(!r.ok){{alert('Nie udało się zapisać kroku. Najpierw wykonaj poprzedni krok w Last Z.');}}await friends()}};parent.appendChild(b)}}
function candidateView(c){{const card=document.createElement('div');card.className='candidate';add(card,'b','Do: '+c.display_name+(c.alliance?' ['+c.alliance+']':''));add(card,'div','Stan: '+c.status);if(c.status==='REVIEW'){{add(card,'p','Proponowany tekst: '+c.draft_message);add(card,'small','Wyślij ręcznie tylko wtedy, gdy się zgadzasz.');actionButton(card,'Oznacz: wysłałem ręcznie',c.id,'offer-sent');actionButton(card,'Nie teraz',c.id,'defer');actionButton(card,'Zły wpływ — blokuj',c.id,'block',null,true)}}else if(c.status==='OFFER_SENT'){{add(card,'p','Czekam na ręczne potwierdzenie znajomości w Last Z.');actionButton(card,'Jesteśmy znajomymi',c.id,'friend-added');actionButton(card,'Zły wpływ — blokuj',c.id,'block',null,true)}}else if(c.status==='NEEDS_ALIAS'){{add(card,'p','Ustaw alias ręcznie na koncie PEva, przełącz PM i potwierdź go tutaj.');const input=document.createElement('input');input.value=c.proposed_alias;input.maxLength=32;card.appendChild(input);actionButton(card,'Alias ustawiony',c.id,'confirm-alias',input);actionButton(card,'Zły wpływ — blokuj',c.id,'block',null,true)}}else if(c.status==='FRIEND'){{add(card,'p','Stała tożsamość PEva: '+(c.final_alias||'brak aliasu'));if(!c.auto_reply_approved){{add(card,'small','To ręcznie potwierdzony alias. Tylko Ty decydujesz, czy PEva może kiedyś automatycznie odpowiadać tej osobie.');actionButton(card,'Zatwierdzam automatyczne odpowiedzi',c.id,'approve-auto-reply')}}else{{add(card,'p','Automatyczne odpowiedzi dla tej osoby: zatwierdzone lokalnie.');actionButton(card,'Cofnij zgodę',c.id,'revoke-auto-reply',null,true)}}actionButton(card,'Usuń z listy PEvy',c.id,'remove',null,true)}}else if(c.status==='DEFERRED'){{add(card,'p','PEva nie zaproponuje ponownie przed dłuższą przerwą.');actionButton(card,'Zły wpływ — blokuj',c.id,'block',null,true)}}else{{add(card,'p','Propozycje dla tej osoby są zablokowane.')}}return card}}
async function friends(){{const root=document.getElementById('friends');const r=await fetch('/api/friends');const payload=await r.json();root.replaceChildren();if(!payload.candidates.length){{root.textContent='Brak kandydatów — PEva nie prosi nikogo automatycznie.';return}}payload.candidates.forEach(c=>root.appendChild(candidateView(c)))}}
preview();friends();setInterval(preview,5000);setInterval(friends,5000)
</script></body></html>"""


def _flow_label(phase: str, reason: str) -> str:
    """Turn internal controller states into one ordinary Polish sentence."""
    if phase == "COLLECTING":
        return "zbiera krótką serię wiadomości"
    if phase == "THINKING":
        return "przygotowuje odpowiedź"
    if phase == "COOLDOWN":
        if reason == "same_visible_turn_already_handled":
            return "ten sam widok wiadomości został już obsłużony"
        return "czeka na nową wiadomość"
    if reason == "local_model_not_ready":
        return "czeka, aż model będzie gotowy"
    if reason == "conversation_covered_by_overlay":
        return "czeka, aż okno zasłaniające czat zniknie"
    return "czeka na prywatną wiadomość"


def _delivery_label(state: str, reason: str) -> str:
    if reason == "composer_has_manual_text":
        return "zatrzymano — wykryto Twój wpis w polu wiadomości"
    if reason == "composer_state_not_confirmed":
        return "zatrzymano — nie udało się pewnie odczytać pola wiadomości"
    if reason == "current_conversation_not_verified":
        return "zatrzymano — nie potwierdzono aktualnej rozmowy"
    if state == "CONFIRMED":
        return "wiadomość PEvy została potwierdzona na ekranie"
    if state == "AMBIGUOUS":
        return "zatrzymano SAFE — wysłanie wymaga ręcznego sprawdzenia"
    return "brak"

