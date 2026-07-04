"""Free AI Chat with OpenAI-compatible API + tool calling support via g4f."""
import json, re, time, uuid
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import g4f
from g4f.Provider import PollinationsAI, Yqcloud
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PROVIDERS = [
    ("PollinationsAI", PollinationsAI, "openai"),
    ("Yqcloud", Yqcloud, "gpt-4o-mini"),
]

MODELS = {
    "gpt-4o-mini": "OpenAI GPT-4o Mini",
    "gpt-4o": "OpenAI GPT-4o",
    "gemini": "Google Gemini",
    "deepseek": "DeepSeek",
    "llama": "Meta Llama",
}

# Regex to match TOOL_CALL: {...} in model output
TOOL_CALL_RE = re.compile(r'TOOL_CALL:\s*(\{.*?\})(?:\n|$)', re.DOTALL)


def _build_tools_system(tools: list) -> str:
    """Build a system message describing available tools."""
    parts = ["You have access to the following tools. When you need to use a tool, "
             "respond with EXACTLY one TOOL_CALL per line like this:",
             'TOOL_CALL: {"name": "tool_name", "arguments": {"key": "value"}}',
             "Do NOT explain what you're doing - just output the TOOL_CALL line(s).",
             "If no tool is needed, respond with normal text.", "",
             "Available tools:"]
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")
        parts.append(f"- {name}: {desc}")
    return "\n".join(parts)


def _parse_tool_calls(text: str) -> list[dict] | None:
    """Parse TOOL_CALL lines from model output. Returns list of tool_calls or None."""
    matches = TOOL_CALL_RE.findall(text)
    if not matches:
        return None
    calls = []
    for i, m in enumerate(matches):
        try:
            parsed = json.loads(m.strip())
            calls.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": parsed.get("name", ""),
                    "arguments": json.dumps(parsed.get("arguments", {}))
                }
            })
        except json.JSONDecodeError:
            continue
    return calls if calls else None


def _strip_tool_calls(text: str) -> str:
    """Remove TOOL_CALL lines from text, returning only the natural language part."""
    return TOOL_CALL_RE.sub("", text).strip()


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": n, "object": "model", "owned_by": "free"} for n in MODELS]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "gpt-4o-mini")
    messages = list(body.get("messages", []))
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    # Inject tool definitions into messages if tools are provided
    if tools:
        tool_system = _build_tools_system(tools)
        # Find the system message or prepend
        sys_idx = -1
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                sys_idx = i
                break
        sys_msg = {"role": "system", "content": tool_system}
        if sys_idx >= 0:
            messages[sys_idx]["content"] += "\n\n" + tool_system
        else:
            messages.insert(0, sys_msg)

    last_err = None
    for prov_name, prov_class, prov_model in PROVIDERS:
        try:
            response = g4f.ChatCompletion.create(
                model=prov_model,
                messages=messages,
                provider=prov_class,
                stream=stream
            )

            if stream:
                async def generate(prov=prov_name):
                    buf = ""
                    for chunk in response:
                        if isinstance(chunk, str) and chunk:
                            buf += chunk
                            # Check if this chunk completes a TOOL_CALL
                            if "TOOL_CALL:" in buf:
                                # For streaming, we send content as-is (client parses it)
                                pass
                            data = {
                                "id": cid,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                            }
                            yield f"data: {json.dumps(data)}\n\n"
                    done = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(done)}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(generate(prov_name), media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            else:
                txt = ""
                for chunk in response:
                    if isinstance(chunk, str):
                        txt += chunk

                # Parse tool calls from the response
                tool_calls = _parse_tool_calls(txt) if tools else None
                content = _strip_tool_calls(txt) if tool_calls else txt

                msg = {"role": "assistant", "content": content or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls

                return JSONResponse({
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                })
        except Exception as e:
            last_err = e
            continue

    err = f"All providers failed. Last error: {last_err}"
    if stream:
        async def err_gen():
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": f"[Error: {err}]"}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")
    return JSONResponse({"error": err}, status_code=502)


PAGE = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Free AI Chat</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d1117;--sf:#161b22;--bd:#30363d;--tx:#e6edf3;--dm:#8b949e;--ac:#58a6ff;--gn:#3fb950;--rd:#f85149}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx);height:100dvh;display:flex;flex-direction:column}
.hd{background:var(--sf);border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;align-items:center;gap:10px}
.hd h1{font-size:16px;font-weight:600}
.hd .st{margin-left:auto;font-size:12px;padding:3px 8px;border-radius:10px;background:var(--gn);color:#000}
select{background:var(--sf);border:1px solid var(--bd);color:var(--tx);padding:5px 8px;border-radius:6px;font-size:12px}
.ch{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.m{max-width:85%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.5;word-wrap:break-word;white-space:pre-wrap}
.m.u{align-self:flex-end;background:var(--ac);color:#fff;border-bottom-right-radius:3px}
.m.a{align-self:flex-start;background:var(--sf);border:1px solid var(--bd);border-bottom-left-radius:3px}
.m .rl{font-size:11px;font-weight:600;margin-bottom:3px;opacity:.6}
.m.er span{color:var(--rd)}
.in{background:var(--sf);border-top:1px solid var(--bd);padding:12px 16px;display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;background:var(--bg);border:1px solid var(--bd);color:var(--tx);padding:8px 12px;border-radius:8px;font-size:14px;font-family:inherit;resize:none;max-height:100px;line-height:1.4}
textarea:focus{outline:none;border-color:var(--ac)}
button{background:var(--ac);color:#fff;border:none;padding:8px 16px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
button:disabled{opacity:.4;cursor:not-allowed}
</style></head><body>
<div class="hd">
<h1>Free AI Chat</h1>
<select id="mdl">
<option value="gpt-4o-mini">GPT-4o Mini</option>
<option value="gpt-4o">GPT-4o</option>
<option value="gemini">Gemini</option>
<option value="deepseek">DeepSeek</option>
<option value="llama">Llama</option>
</select>
<span class="st">Ready</span>
</div>
<div class="ch" id="ch">
<div style="text-align:center;color:var(--dm);padding:40px" id="wl">
<div style="font-size:42px;margin-bottom:12px">&#x1F916;</div>
<h2 style="margin-bottom:6px">Free AI Chat</h2>
<p style="font-size:13px">Powered by free AI models</p>
</div>
</div>
<div class="in">
<textarea id="pr" placeholder="Type a message..." rows="1"></textarea>
<button id="sb" onclick="send()">Send</button>
</div>
<script>
const ch=document.getElementById('ch'),pr=document.getElementById('pr'),sb=document.getElementById('sb'),
mdl=document.getElementById('mdl'),wl=document.getElementById('wl');
let model=mdl.value,busy=false,msgs=[];
mdl.onchange=()=>{model=mdl.value};
pr.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}};
pr.oninput=()=>{pr.style.height='auto';pr.style.height=Math.min(pr.scrollHeight,100)+'px'};

async function send(){
  const t=pr.value.trim();if(!t||busy)return;
  busy=true;sb.disabled=true;pr.value='';pr.style.height='auto';
  if(wl)wl.remove();
  addM('u',t);msgs.push({role:'user',content:t});
  const ai=addM('a','...',model);const sp=ai.querySelector('.ct');let f='';
  try{
    const r=await fetch('/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model,messages:msgs,stream:true})});
    const rd=r.body.getReader(),dc=new TextDecoder();let b='';
    while(true){const{done,value}=await rd.read();if(done)break;
    b+=dc.decode(value,{stream:true});const ls=b.split('\n');b=ls.pop();
    for(const l of ls){if(!l.startsWith('data: '))continue;const d=l.slice(6);
    if(d==='[DONE]')break;
    try{const j=JSON.parse(d);
    const dl=j.choices&&j.choices[0]&&j.choices[0].delta&&j.choices[0].delta.content;
    if(dl){f+=dl;sp.textContent=f;ch.scrollTop=ch.scrollHeight}}catch(e){}}}
    if(!f){sp.textContent='[No response]';ai.classList.add('er')}
    msgs.push({role:'assistant',content:f});
  }catch(e){sp.textContent='[Error: '+e.message+']';ai.classList.add('er')}
  busy=false;sb.disabled=false;pr.focus();
}
function addM(role,content,mdl){
  const d=document.createElement('div');d.className='m '+role;
  if(role==='a'){const rl=document.createElement('div');rl.className='rl';rl.textContent=mdl||'AI';d.appendChild(rl)}
  const s=document.createElement('span');s.className='ct';s.textContent=content;d.appendChild(s);
  ch.appendChild(d);ch.scrollTop=ch.scrollHeight;return d;
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE


if __name__ == "__main__":
    print("=" * 50)
    print("  Free AI Chat Server (with tool calling support)")
    print("  Open http://localhost:9191")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=9191)
