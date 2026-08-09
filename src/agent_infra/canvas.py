from __future__ import annotations

import hashlib
import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .codec import canonical_json, load_document, pretty_json
from .errors import AgentInfraError
from .schema import WORKFLOW_SCHEMA
from .validation import validate_document

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>REAL Workflow Canvas</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui;background:#0d1117;color:#e6edf3}*{box-sizing:border-box}
body{margin:0;height:100vh;display:grid;grid-template-rows:52px 1fr}header{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid #30363d;background:#161b22}
button,select,input,textarea{color:#e6edf3;background:#21262d;border:1px solid #3d444d;border-radius:6px;padding:7px}button{cursor:pointer}button.primary{background:#238636;border-color:#2ea043}.spacer{flex:1}.status{font-size:13px;color:#8b949e}
main{min-height:0;display:grid;grid-template-columns:1fr 320px}.workspace{position:relative;overflow:auto;background-color:#0d1117;background-image:radial-gradient(#30363d 1px,transparent 1px);background-size:20px 20px}
#edges{position:absolute;inset:0;width:2400px;height:1600px;pointer-events:none}.node{position:absolute;width:180px;min-height:72px;background:#161b22;border:1px solid #3d444d;border-radius:9px;box-shadow:0 5px 18px #0008;user-select:none}.node.selected{border-color:#58a6ff;box-shadow:0 0 0 2px #1f6feb55}.node h3{font-size:14px;margin:0;padding:11px;border-bottom:1px solid #30363d}.node p{font-size:12px;color:#8b949e;margin:9px 11px}.node.entry h3:before{content:'▶ ';color:#3fb950}
aside{border-left:1px solid #30363d;background:#161b22;padding:14px;overflow:auto}label{display:block;font-size:12px;color:#8b949e;margin:12px 0 4px}textarea{width:100%;min-height:170px;font-family:ui-monospace,monospace;font-size:12px}.row{display:flex;gap:8px}.row>*{flex:1}.danger{color:#ff7b72}.issues{white-space:pre-wrap;font-size:12px;color:#ff7b72;margin-top:12px}.hidden{display:none!important}#source{position:absolute;inset:12px;width:calc(100% - 24px);height:calc(100% - 24px);resize:none}
</style></head>
<body><header><strong>REAL Workflow Canvas</strong><button id="graphTab">Graph</button><button id="sourceTab">Source</button><select id="newType"><option>template</option><option>llm</option><option>tool</option><option>subworkflow</option><option>branch</option><option>join</option><option>constant</option><option>output</option><option>passthrough</option></select><button id="addNode">Add node</button><button id="addEdge">Add edge</button><span class="spacer"></span><span class="status" id="status">Loading…</span><button class="primary" id="save">Validate & Save</button></header>
<main><section class="workspace" id="workspace"><svg id="edges"></svg><textarea class="hidden" id="source" spellcheck="false"></textarea></section><aside id="inspector"><strong>Node inspector</strong><div id="empty">Select a node to edit it.</div><div id="form" class="hidden"><label>ID</label><input id="nodeId"><label>Type</label><select id="nodeType"><option>constant</option><option>template</option><option>tool</option><option>llm</option><option>subworkflow</option><option>branch</option><option>join</option><option>output</option><option>passthrough</option></select><label>Config (JSON)</label><textarea id="nodeConfig"></textarea><div class="row"><button id="applyNode">Apply</button><button class="danger" id="deleteNode">Delete</button></div></div><div class="issues" id="issues"></div></aside></main>
<script>
let doc, etag, selected=null, sourceMode=false; const $=id=>document.getElementById(id);
const positions=()=>{doc.metadata??={};doc.metadata.canvas??={};return doc.metadata.canvas.positions??={}};
async function load(){let r=await fetch('/api/workflow');doc=await r.json();etag=r.headers.get('etag');$('source').value=JSON.stringify(doc,null,2);render();$('status').textContent=doc.name+' '+doc.version}
function render(){document.querySelectorAll('.node').forEach(x=>x.remove());$('edges').innerHTML='';let pos=positions();doc.nodes.forEach((n,i)=>{pos[n.id]??={x:80+(i%4)*230,y:70+Math.floor(i/4)*150};let el=document.createElement('div');el.className='node'+(n.id===selected?' selected':'')+(n.id===doc.entry?' entry':'');el.dataset.id=n.id;el.style.left=pos[n.id].x+'px';el.style.top=pos[n.id].y+'px';el.innerHTML='<h3>'+escapeHtml(n.id)+'</h3><p>'+escapeHtml(n.type)+'</p>';el.onclick=()=>select(n.id);drag(el,n.id);$('workspace').append(el)});drawEdges()}
function escapeHtml(s){let d=document.createElement('div');d.textContent=s;return d.innerHTML}
function drawEdges(){let pos=positions();doc.edges.forEach(e=>{if(!pos[e.source]||!pos[e.target])return;let p=document.createElementNS('http://www.w3.org/2000/svg','path'),a=pos[e.source],b=pos[e.target],x1=a.x+180,y1=a.y+36,x2=b.x,y2=b.y+36,c=(x1+x2)/2;p.setAttribute('d',`M${x1},${y1} C${c},${y1} ${c},${y2} ${x2},${y2}`);p.setAttribute('fill','none');p.setAttribute('stroke',e.when?'#d29922':'#58a6ff');p.setAttribute('stroke-width','2');$('edges').append(p)})}
function drag(el,id){let start;el.onpointerdown=e=>{if(e.target.tagName==='BUTTON')return;let p=positions()[id];start={clientX:e.clientX,clientY:e.clientY,nodeX:p.x,nodeY:p.y};el.setPointerCapture(e.pointerId)};el.onpointermove=e=>{if(!start)return;positions()[id]={x:Math.max(0,start.nodeX+e.clientX-start.clientX),y:Math.max(0,start.nodeY+e.clientY-start.clientY)};el.style.left=positions()[id].x+'px';el.style.top=positions()[id].y+'px';drawEdges()};el.onpointerup=()=>start=null}
function select(id){selected=id;let n=doc.nodes.find(n=>n.id===id);$('empty').classList.add('hidden');$('form').classList.remove('hidden');$('nodeId').value=n.id;$('nodeType').value=n.type;$('nodeConfig').value=JSON.stringify(n.config??{},null,2);render()}
$('applyNode').onclick=()=>{let n=doc.nodes.find(n=>n.id===selected),old=n.id,newId=$('nodeId').value.trim();try{n.config=JSON.parse($('nodeConfig').value)}catch(e){return showIssues([{path:'config',message:e.message}])}n.id=newId;n.type=$('nodeType').value;if(old!==newId){doc.edges.forEach(e=>{if(e.source===old)e.source=newId;if(e.target===old)e.target=newId});if(doc.entry===old)doc.entry=newId;positions()[newId]=positions()[old];delete positions()[old];selected=newId}render()};
$('deleteNode').onclick=()=>{doc.nodes=doc.nodes.filter(n=>n.id!==selected);doc.edges=doc.edges.filter(e=>e.source!==selected&&e.target!==selected);delete positions()[selected];selected=null;$('form').classList.add('hidden');$('empty').classList.remove('hidden');render()};
$('addNode').onclick=()=>{let base=$('newType').value,id=base,i=1;while(doc.nodes.some(n=>n.id===id))id=base+(++i);let defaults={template:{template:'${$.input.message}'},llm:{provider:'provider',provider_version:'1',model:'model',model_version:'model',prompt:'${$.input.message}'},tool:{tool:'tool',tool_version:'1',arguments:{}},branch:{value:'${$.input.value}'},join:{wait_for:[]},constant:{value:null},output:{value:'${$.nodes.'+(doc.nodes.at(-1)?.id??'node')+'}'},passthrough:{},subworkflow:{plan_digest:'',input:'${$.input}'}};doc.nodes.push({id,type:base,config:defaults[base]});select(id)};
$('addEdge').onclick=()=>{let source=prompt('Source node ID',selected??''),target=prompt('Target node ID','');if(source&&target){doc.edges.push({source,target});render()}};
$('sourceTab').onclick=()=>{sourceMode=true;$('source').value=JSON.stringify(doc,null,2);$('source').classList.remove('hidden');$('edges').classList.add('hidden');document.querySelectorAll('.node').forEach(x=>x.classList.add('hidden'))};
$('graphTab').onclick=()=>{if(sourceMode){try{doc=JSON.parse($('source').value)}catch(e){return showIssues([{path:'$',message:e.message}])}}sourceMode=false;$('source').classList.add('hidden');$('edges').classList.remove('hidden');render()};
function showIssues(items){$('issues').textContent=items.map(x=>x.path+': '+x.message).join('\n')}
$('save').onclick=async()=>{if(sourceMode){try{doc=JSON.parse($('source').value)}catch(e){return showIssues([{path:'$',message:e.message}])}}let r=await fetch('/api/workflow',{method:'PUT',headers:{'content-type':'application/json','if-match':etag},body:JSON.stringify(doc)}),body=await r.json();if(!r.ok){showIssues(body.issues??[{path:'$',message:body.error}]);$('status').textContent='Not saved';return}etag=r.headers.get('etag');showIssues([]);$('status').textContent='Saved '+new Date().toLocaleTimeString();doc=body.workflow;render()};load();
</script></body></html>"""


class CanvasServer:
    def __init__(self, source: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise AgentInfraError("Canvas may only bind to loopback because it edits local source files")
        self.source = Path(source).resolve()
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "REALCanvas/0.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, body: bytes, content_type: str, etag: str | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if etag:
                    self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, value: Any, etag: str | None = None) -> None:
                self._send(status, pretty_json(value).encode(), "application/json; charset=utf-8", etag)

            def do_GET(self) -> None:
                if self.path == "/":
                    self._send(HTTPStatus.OK, HTML.encode(), "text/html; charset=utf-8")
                elif self.path == "/api/schema":
                    self._json(HTTPStatus.OK, WORKFLOW_SCHEMA)
                elif self.path == "/api/workflow":
                    try:
                        value = load_document(outer.source)
                        self._json(HTTPStatus.OK, value, outer.etag(value))
                    except Exception as exc:
                        self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_PUT(self) -> None:
                if self.path != "/api/workflow":
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 2_000_000:
                        raise ValueError("workflow document is too large")
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError("workflow must be an object")
                    issues = validate_document(value)
                    if issues:
                        self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"issues": [x.to_dict() for x in issues]})
                        return
                    with outer.lock:
                        current = load_document(outer.source)
                        if self.headers.get("If-Match") != outer.etag(current):
                            self._json(
                                HTTPStatus.PRECONDITION_FAILED, {"error": "source changed; reload before saving"}
                            )
                            return
                        outer.source.write_text(pretty_json(value), encoding="utf-8")
                    self._json(HTTPStatus.OK, {"ok": True, "workflow": value}, outer.etag(value))
                except (ValueError, json.JSONDecodeError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.port = self.httpd.server_address[1]

    @staticmethod
    def etag(value: dict[str, Any]) -> str:
        return '"' + hashlib.sha256(canonical_json(value).encode()).hexdigest() + '"'

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def serve(self, *, open_browser: bool = False) -> None:
        if open_browser:
            webbrowser.open(self.url)
        self.httpd.serve_forever()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
