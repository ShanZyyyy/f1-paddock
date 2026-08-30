# -*- coding: utf-8 -*-
"""Ana sayfa açılış hero'su — duman + 'FORMULA PADDOCK' + sıradaki seans sayacı.

`components.html` (iframe) ile gömülür; JS/WebGL bu yüzden şart. Yazı ekranın
tam ortasında belirir, sol-üste (Streamlit üst barındaki logonun hizasına)
süzülür ve kaybolur; arkada gerçek verili geri sayım kalır.

Aynı sekme oturumunda tekrar render'da (her rerun'da) açılış OYNAMAZ —
`sessionStorage` ile doğrudan dinlenme hâli gelir. Yeni sekme / sert
yenileme açılışı tekrar gösterir.
"""

import datetime
import streamlit as st
import streamlit.components.v1 as components

from core import ui as _ui


_TEMPLATE = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Saira:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{
  --ink:#07090d;--ink-deep:#030405;--steel:#c9d5e2;--steel-dim:#8b9bad;--rush:#e10600;
  --text:#f2f5f8;--text-dim:#9fb0c0;--text-mute:#63748a;--line:#26313f;
  --f-display:'Antonio','Arial Narrow',sans-serif;--f-body:'Saira',system-ui,sans-serif;
  --f-mono:'JetBrains Mono',ui-monospace,monospace;--px:0px;--py:0px;
  --logo-home:translate(-50%,-50%) translate(calc(-50vw + 2rem + 88px), calc(-50vh + 1.1rem));
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--ink-deep);color:var(--text);font-family:var(--f-body);
  -webkit-font-smoothing:antialiased;overflow:hidden}
.stage{position:relative;width:100%;height:100vh;min-height:520px;overflow:hidden;isolation:isolate;
  background:linear-gradient(180deg,var(--ink),var(--ink-deep))}
#smoke{position:absolute;inset:-3%;width:106%;height:106%;z-index:2;display:block;
  transform:translate(calc(var(--px)*-1),calc(var(--py)*-1))}
.bloom{position:absolute;left:50%;top:47%;width:96vw;height:78vh;z-index:3;pointer-events:none;
  transform:translate(-50%,-50%);mix-blend-mode:screen;opacity:.85;
  background:radial-gradient(ellipse 46% 52% at 50% 50%,rgba(255,247,236,.16),rgba(233,240,251,.09) 34%,rgba(142,169,198,.05) 56%,transparent 74%);
  animation:breathe 9s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:.72;transform:translate(-50%,-50%) scale(1)}50%{opacity:.95;transform:translate(-50%,-50%) scale(1.05)}}
.stage.play .bloom{animation:bloomIn 3.8s ease-out both, breathe 9s ease-in-out infinite 3.8s}
@keyframes bloomIn{0%{opacity:.1;transform:translate(-50%,-50%) scale(.5)}60%{opacity:1;transform:translate(-50%,-50%) scale(1.08)}100%{opacity:.82;transform:translate(-50%,-50%) scale(1)}}

.wordmark{position:absolute;left:50%;top:50%;z-index:20;pointer-events:none;display:flex;align-items:center;
  gap:.55em;font-family:var(--f-display);font-weight:700;font-size:1.02rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--text);white-space:nowrap;transform:var(--logo-home);opacity:0}
.wordmark svg{width:.92em;height:.92em;flex:0 0 auto}
.wordmark b{color:var(--rush);font-weight:700}
.stage.play .wordmark{animation:reveal 3.9s cubic-bezier(.5,0,.15,1) .3s both}
@keyframes reveal{
  0%{opacity:0;font-size:clamp(1.7rem,4.6vw,4rem);letter-spacing:.34em;transform:translate(-50%,-50%);filter:blur(32px)}
  11%{opacity:1;font-size:clamp(1.7rem,4.6vw,4rem);letter-spacing:.16em;transform:translate(-50%,-50%);filter:blur(0)}
  70%{opacity:1;font-size:clamp(1.7rem,4.6vw,4rem);letter-spacing:.16em;transform:translate(-50%,-50%);filter:blur(0)}
  100%{opacity:1;font-size:1.02rem;letter-spacing:.14em;transform:var(--logo-home);filter:blur(0)}
}
.stage.settled .wordmark{display:none}

.hud{position:absolute;right:clamp(1.2rem,5vw,3.4rem);top:50%;transform:translateY(-50%);z-index:12;
  width:min(360px,44vw);opacity:0;font-family:var(--f-mono);border:1px solid var(--line);
  border-left:3px solid var(--rush);background:linear-gradient(160deg,rgba(17,22,31,.66),rgba(12,16,22,.5));
  -webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);padding:1.25rem 1.4rem}
.hud-top{display:flex;justify-content:space-between;align-items:baseline;gap:1rem}
.hud-k{font-size:.6rem;letter-spacing:.22em;color:var(--text-mute)}
.hud-ev{font-family:var(--f-display);font-weight:700;font-size:.92rem;letter-spacing:.06em;color:var(--text)}
.hud-c{display:flex;align-items:flex-start;gap:.45rem;margin:.85rem 0 .7rem}
.hud-c>div{display:flex;flex-direction:column;align-items:center;gap:.3rem}
.hud-c b{font-size:1.8rem;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums;line-height:1}
.hud-c s{font-size:.5rem;letter-spacing:.14em;color:var(--text-mute);text-decoration:none}
.hud-c .sep{font-size:1.3rem;color:var(--text-mute);padding-top:.15rem}
.hud-bar{height:2px;background:rgba(201,213,226,.12);overflow:hidden;margin-bottom:.6rem}
.hud-bar i{display:block;height:100%;width:38%;background:linear-gradient(90deg,var(--rush),#ff5b3d)}
.hud-foot{font-size:.54rem;letter-spacing:.1em;color:var(--text-mute)}
.hud.live .hud-k::after{content:" · CANLI";color:var(--rush)}
.stage.play .hud{animation:hudIn 1s cubic-bezier(.2,.7,.2,1) 4.05s both}
@keyframes hudIn{0%{opacity:0;transform:translateY(-50%) translateX(44px)}100%{opacity:1;transform:translateY(-50%) translateX(0)}}
.stage.settled .hud{opacity:1;transform:translateY(-50%);animation:none}

.tag-block{position:absolute;left:clamp(1.5rem,6vw,5rem);bottom:clamp(1.6rem,6vh,3.6rem);z-index:12;
  max-width:min(48rem,88vw);opacity:0}
.tag{font-family:var(--f-display);font-weight:700;
  font-size:min(clamp(2.7rem,8.6vw,7.4rem),12.5vh);line-height:.9;
  letter-spacing:-.015em;text-transform:uppercase;color:var(--text)}
.tag span{display:block;color:var(--steel-dim)}
.tag .p{color:var(--text)}
.tag-sub{margin-top:1.15rem;font-family:var(--f-body);font-weight:400;
  font-size:clamp(.92rem,1.5vw,1.12rem);line-height:1.55;letter-spacing:.01em;
  color:var(--text-dim);max-width:44ch}
.stage.play .tag-block{animation:tagIn .9s cubic-bezier(.2,.7,.2,1) 4.1s both}
@keyframes tagIn{0%{opacity:0;transform:translateY(16px)}100%{opacity:1;transform:translateY(0)}}
.stage.settled .tag-block{opacity:1;transform:none;animation:none}

.scrim{position:absolute;inset:0;z-index:5;pointer-events:none;
  background:linear-gradient(100deg,rgba(3,4,6,.78) 0%,rgba(3,4,6,.4) 30%,rgba(3,4,6,0) 62%)}
.vig{position:absolute;inset:0;z-index:7;pointer-events:none;
  box-shadow:inset 0 0 200px 60px rgba(0,0,0,.62), inset 0 -90px 130px -50px rgba(0,0,0,.5)}
.skip{position:absolute;left:clamp(1rem,4vw,2.4rem);bottom:1rem;z-index:25;font-family:var(--f-mono);
  font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;color:var(--text-mute);background:none;
  border:1px solid var(--line);padding:.5rem .8rem;cursor:pointer;opacity:0;transition:opacity .3s,color .2s,border-color .2s}
.stage.play .skip{opacity:1}.stage.settled .skip{display:none}
.skip:hover{color:var(--text);border-color:var(--steel-dim)}
@media(max-width:580px){.hud{display:none}}
@media(prefers-reduced-motion:reduce){
  .stage.play .wordmark,.stage.play .hud,.stage.play .bloom,.stage.play .tag-block,.bloom{animation:none!important}
  .wordmark{display:none}
  .hud{opacity:1!important;transform:translateY(-50%)!important}.bloom{opacity:.7!important}
  .tag-block{opacity:1!important;transform:none!important}
  .skip{display:none}
}
</style></head>
<body>
<main class="stage" id="stage">
  <canvas id="smoke"></canvas>
  <div class="bloom"></div>
  <div class="wordmark">
    <svg viewBox="0 0 48 48"><path d="M13 11 L27 24 L13 37" fill="none" stroke="#e10600" stroke-width="6.5" stroke-linecap="square"/><path d="M24.5 15 L33.5 24 L24.5 33" fill="none" stroke="#e10600" stroke-width="5" stroke-linecap="square" opacity=".5"/></svg>
    FORMULA&nbsp;<b>PADDOCK</b>
  </div>
  <div class="scrim"></div>
  <div class="tag-block">
    <h1 class="tag"><span>Veriyle</span><span>konuşur.</span><span class="p">Uydurmaz.</span></h1>
    <p class="tag-sub">Buraya alt açıklama metni gelecek</p>
  </div>
  <aside class="hud __LIVECLS__" aria-label="Sıradaki seans">
    <div class="hud-top"><span class="hud-k">SIRADAKI SEANS</span><span class="hud-ev">__EVENT__</span></div>
    <div class="hud-c">
      <div><b id="d">--</b><s>GÜN</s></div><span class="sep">:</span>
      <div><b id="h">--</b><s>SAAT</s></div><span class="sep">:</span>
      <div><b id="m">--</b><s>DK</s></div><span class="sep">:</span>
      <div><b id="s">--</b><s>SN</s></div>
    </div>
    <div class="hud-bar"><i></i></div>
    <div class="hud-foot">__FOOT__</div>
  </aside>
  <div class="vig"></div>
  <button class="skip" id="skip" type="button">geç →</button>
</main>
<script>
(function(){
"use strict";
var stage=document.getElementById('stage');
var reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
var TARGET=__TARGET_MS__, LIVE=__LIVE__;

/* geri sayım */
function pad(n){return String(n).padStart(2,'0');}
function cd(){
  var ms=Math.max(0,TARGET-Date.now());
  document.getElementById('d').textContent=pad(Math.floor(ms/864e5));
  document.getElementById('h').textContent=pad(Math.floor(ms/36e5)%24);
  document.getElementById('m').textContent=pad(Math.floor(ms/6e4)%60);
  document.getElementById('s').textContent=pad(Math.floor(ms/1e3)%60);
}
if(TARGET>0){cd();setInterval(cd,1000);}

/* WebGL fBm duman */
var Smoke=(function(){
  var cv=document.getElementById('smoke');
  var gl=cv.getContext('webgl')||cv.getContext('experimental-webgl');
  if(!gl){cv.style.background='radial-gradient(85% 65% at 60% 34%,#33465c55,transparent 60%),linear-gradient(180deg,#0c1017,#050608)';return {start:function(){}};}
  var vs='attribute vec2 a;varying vec2 v;void main(){v=a*.5+.5;gl_Position=vec4(a,0.,1.);}';
  var fs=['precision highp float;varying vec2 v;uniform vec2 R;uniform float T;',
   'float h(vec2 p){p=fract(p*vec2(123.34,345.45));p+=dot(p,p+34.345);return fract(p.x*p.y);}',
   'float n(vec2 p){vec2 i=floor(p),f=fract(p);float a=h(i),b=h(i+vec2(1,0)),c=h(i+vec2(0,1)),d=h(i+vec2(1,1));',
   'vec2 u=f*f*(3.-2.*f);return mix(a,b,u.x)+(c-a)*u.y*(1.-u.x)+(d-b)*u.x*u.y;}',
   'float fbm(vec2 p){float s=0.,a=.5;mat2 m=mat2(.8,.6,-.6,.8);for(int i=0;i<6;i++){s+=a*n(p);p=m*p*2.+.03;a*=.5;}return s;}',
   'void main(){vec2 uv=v;vec2 p=uv*vec2(R.x/R.y,1.)*2.;float t=T*.05;',
   'vec2 q=vec2(fbm(p+vec2(0.,t)),fbm(p+vec2(5.2,1.3-t)));',
   'vec2 r=vec2(fbm(p+2.*q+vec2(1.7,9.2)+t*.7),fbm(p+2.*q+vec2(8.3,2.8)-t*.5));',
   'float f=fbm(p+1.8*r);float d=smoothstep(.24,.88,f);d*=smoothstep(.0,.55,f+.2);d=pow(d,1.12);',
   'float big=fbm(p*.55+vec2(t*.35,1.7));d=max(d,smoothstep(.46,.95,big)*.55);',
   'float mx=smoothstep(-.2,1.05,uv.x),my=smoothstep(-.15,.95,1.-uv.y);',
   'float mask=mix(.26,1.,clamp(mx*.5+my*.6,0.,1.));d*=mask;',
   'vec3 lo=vec3(.06,.08,.115),hi=vec3(.3,.35,.44);vec3 col=mix(lo,hi,d);',
   'float warm=smoothstep(.45,1.,uv.x)*smoothstep(.4,1.,1.-uv.y);col+=vec3(.12,.02,0.)*warm*d;',
   'col+=(h(gl_FragCoord.xy+T)-.5)*.016;gl_FragColor=vec4(col,clamp(d*1.04,0.,1.));}'].join('\n');
  function sh(t,s){var x=gl.createShader(t);gl.shaderSource(x,s);gl.compileShader(x);return x;}
  var pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));
  gl.linkProgram(pr);gl.useProgram(pr);
  var b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
  var la=gl.getAttribLocation(pr,'a');gl.enableVertexAttribArray(la);gl.vertexAttribPointer(la,2,gl.FLOAT,false,0,0);
  var uR=gl.getUniformLocation(pr,'R'),uT=gl.getUniformLocation(pr,'T'),t0=performance.now(),raf=0,run=false;
  function rs(){var s=.6;cv.width=Math.max(2,innerWidth*s|0);cv.height=Math.max(2,innerHeight*s|0);
    gl.viewport(0,0,cv.width,cv.height);gl.uniform2f(uR,cv.width,cv.height);}
  addEventListener('resize',rs);rs();
  function draw(now){gl.uniform1f(uT,(now-t0)/1000);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);}
  function loop(now){if(!run)return;draw(now);raf=requestAnimationFrame(loop);}
  return {start:function(){if(reduce){draw(performance.now());return;}run=true;cancelAnimationFrame(raf);raf=requestAnimationFrame(loop);}};
})();

/* yumuşatılmış parallax */
if(!reduce && matchMedia('(pointer:fine)').matches){
  var tx=0,ty=0,cx=0,cy=0;
  addEventListener('mousemove',function(e){tx=e.clientX/innerWidth-.5;ty=e.clientY/innerHeight-.5;});
  (function p(){cx+=(tx-cx)*.045;cy+=(ty-cy)*.045;
    stage.style.setProperty('--px',(cx*26).toFixed(1)+'px');
    stage.style.setProperty('--py',(cy*26).toFixed(1)+'px');requestAnimationFrame(p);})();
}

/* açılış yalnızca yeni sekme/sert yenilemede — rerun'da direkt dinlenme */
var seen=false;try{seen=sessionStorage.getItem('fp_hero_played')==='1';}catch(e){}
function settle(){stage.classList.add('play','settled');Smoke.start();}
function play(){
  stage.classList.remove('settled');stage.classList.add('play');Smoke.start();
  try{sessionStorage.setItem('fp_hero_played','1');}catch(e){}
  setTimeout(function(){stage.classList.add('settled');},6200);  /* açılış bitince dinlenmeye geç */
}
if(reduce||seen){settle();}else{requestAnimationFrame(play);}
setTimeout(function(){if(!stage.classList.contains('play'))settle();},3500);
document.getElementById('skip').addEventListener('click',settle);
})();
</script>
</body></html>"""


def _fmt_foot(is_live):
    return "canlı yayında" if is_live else "seans saati doğrulandı · TSİ"


def render(event_name, session_name, target_dt, is_live, height=680):
    """Hero splash'i gömer. ``target_dt`` UTC datetime (veya None)."""
    if target_dt is not None:
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=datetime.timezone.utc)
        target_ms = int(target_dt.timestamp() * 1000)
    else:
        target_ms = 0

    label = f"{event_name} · {session_name}".strip(" ·") or "Takvim bekleniyor"
    html = (
        _TEMPLATE
        .replace("__TARGET_MS__", str(target_ms))
        .replace("__LIVE__", "true" if is_live else "false")
        .replace("__LIVECLS__", "live" if is_live else "")
        .replace("__EVENT__", _ui.safe_html(label))
        .replace("__FOOT__", _ui.safe_html(_fmt_foot(is_live)))
    )
    # tam-genişlik işareti: üst bar CSS'i bu işareti izleyen konteyneri
    # ekran kenarına yayar (Streamlit block-container padding'ini iptal eder).
    st.markdown('<div class="fp-hero-mark"></div>', unsafe_allow_html=True)
    components.html(html, height=height, scrolling=False)
