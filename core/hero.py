# -*- coding: utf-8 -*-
"""Ana sayfa açılış hero'su — duman + 'FORMULA PADDOCK' + sıradaki seans panosu.

`components.html` (iframe) ile gömülür; JS/WebGL bu yüzden şart. Yazı ekranın
tam ortasında belirir, sol-üste (Streamlit üst barındaki logonun hizasına)
süzülür ve kaybolur; arkada canlı pist (kendini çizen tur + turda dolanan iki
araç) ve gerçek verili geri sayım panosu kalır.

Aynı sekme oturumunda tekrar render'da (her rerun'da) açılış OYNAMAZ —
`sessionStorage` ile doğrudan dinlenme hâli gelir. Yeni sekme / sert
yenileme açılışı tekrar gösterir.
"""

import datetime
import streamlit as st
import streamlit.components.v1 as components

from core import ui as _ui


# Slogan altındaki açıklama — tek yerden değiştirilir.
_DEFAULT_SUB = (
    "Canlı zamanlama, resmî sonuçlar ve doğrulanmış paddock verisi — "
    "tahmin değil, kaynağından kayıt."
)


_TEMPLATE = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Saira:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
:root{
  --ink:#07090d;--ink-deep:#030405;--steel:#c9d5e2;--steel-dim:#8b9bad;--rush:#e10600;
  --text:#f2f5f8;--text-dim:#9fb0c0;--text-mute:#63748a;--line:#26313f;
  --info:#38e1d0;--caution:#f5c33b;--go:#4ade80;
  --f-display:'Antonio','Arial Narrow',sans-serif;--f-body:'Saira',system-ui,sans-serif;
  --f-mono:'JetBrains Mono',ui-monospace,monospace;--px:0px;--py:0px;--ch:14px;
  --dot:radial-gradient(rgba(125,145,165,.11) 1px,transparent 1.6px);--dot-size:13px 13px;
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

/* ---- canlı pist (kendini çizen tur + turda dolanan iki araç) ---- */
.telemetry{position:absolute;inset:0;z-index:4;pointer-events:none;overflow:hidden}
.circuit{position:absolute;left:52%;top:50%;transform:translate(-50%,-50%);
  width:min(126%,1480px);height:auto;z-index:6;opacity:0;
  -webkit-mask-image:radial-gradient(155% 135% at 60% 44%,#000 34%,transparent 94%);
  mask-image:radial-gradient(155% 135% at 60% 44%,#000 34%,transparent 94%)}
.circuit .trk{fill:none;stroke:var(--info)}
.circuit .ghost{stroke:var(--info);opacity:.3;stroke-width:2.5}
.circuit .draw{stroke-dasharray:3520;stroke-dashoffset:3520}
.circuit .pulse{opacity:.85;stroke-width:4.5;stroke-linecap:round;stroke-dasharray:150 3370;animation:fpChase 7s linear infinite}
.circuit .pulse2{opacity:.5;stroke:var(--rush);stroke-width:3.5;stroke-linecap:round;stroke-dasharray:80 3440;animation:fpChase 11s linear infinite reverse}
@keyframes fpChase{to{stroke-dashoffset:-3520}}
.circuit .grid{stroke:var(--info);opacity:.05;stroke-width:1}
.circuit .sf{stroke:#f2f5f8;opacity:.3;stroke-width:5}
.stage.play .circuit .draw{animation:fpDraw 3.4s ease-out .2s forwards}
@keyframes fpDraw{to{stroke-dashoffset:0}}
.stage.play .circuit{animation:circIn 1.5s ease-out 3.5s both}
@keyframes circIn{from{opacity:0}to{opacity:.72}}
.stage.settled .circuit{opacity:.72;animation:none}
.stage.settled .circuit .draw{stroke-dashoffset:0}
.datastream{position:absolute;left:0;right:0;font-family:var(--f-mono);font-size:.64rem;
  letter-spacing:.08em;color:var(--text-dim);opacity:.12;white-space:nowrap}
.datastream span{display:inline-block;padding-right:64px}
.ds1{top:13%;animation:dsScroll 36s linear infinite}
.ds2{top:71%;animation:dsScroll 48s linear infinite reverse}
@keyframes dsScroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}

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

/* ---- merkezî pano (sıradaki seans) ---- */
.dash{position:absolute;right:clamp(1rem,5vw,3.2rem);top:47%;transform:translateY(-50%);z-index:12;
  width:min(360px,42vw);padding:20px 22px 18px;opacity:0;
  background-color:rgba(12,16,22,.72);background-image:var(--dot);background-size:var(--dot-size);
  -webkit-backdrop-filter:blur(7px);backdrop-filter:blur(7px);
  clip-path:polygon(var(--ch) 0,100% 0,100% calc(100% - var(--ch)),calc(100% - var(--ch)) 100%,0 100%,0 var(--ch));
  box-shadow:inset 0 0 0 1px var(--line), inset 3px 0 0 var(--rush)}
.dash-hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  font-family:var(--f-mono);font-size:.58rem;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:var(--text-mute)}
.dash-hd b{font-family:var(--f-display);font-weight:700;font-size:.95rem;letter-spacing:.05em;color:var(--text)}
.dash.live .dash-hd span::after{content:" · CANLI";color:var(--rush)}
.dial{position:relative;width:196px;height:196px;margin:12px auto 4px}
.dial svg{width:100%;height:100%;transform:rotate(-90deg)}
.dial .track{stroke:var(--line);stroke-width:6;fill:none}
.dial .tick{stroke:rgba(125,145,165,.22);stroke-width:2}
.dial .arcR{stroke:var(--rush);stroke-width:6;fill:none;stroke-linecap:round;transition:stroke-dashoffset .5s linear}
.dial .arcA{stroke:var(--caution);stroke-width:4;fill:none;stroke-linecap:round;transition:stroke-dashoffset .5s linear}
.dial .arcC{stroke:var(--info);stroke-width:4;fill:none;stroke-linecap:round;transition:stroke-dashoffset .5s linear}
.dial .ctr{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
.dial .ctr b{font-family:var(--f-mono);font-weight:700;font-size:2.9rem;line-height:1;color:var(--text);font-variant-numeric:tabular-nums}
.dial .ctr s{font-family:var(--f-mono);font-size:.55rem;letter-spacing:.24em;color:var(--text-mute);text-decoration:none}
.dash-row{display:flex;justify-content:center;gap:16px;font-family:var(--f-mono);margin-top:2px}
.dash-row div{display:flex;flex-direction:column;align-items:center;gap:3px}
.dash-row b{font-size:1.15rem;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums}
.dash-row s{font-size:.5rem;letter-spacing:.16em;color:var(--text-mute);text-decoration:none}
.dash-ft{margin-top:12px;padding-top:10px;border-top:1px solid var(--line);
  display:flex;align-items:center;gap:8px;font-family:var(--f-mono);font-size:.54rem;letter-spacing:.1em;color:var(--text-mute)}
.dash-ft .lv{width:6px;height:6px;border-radius:50%;background:var(--go);box-shadow:0 0 0 3px rgba(74,222,128,.16)}
.dash.live .dash-ft .lv{background:var(--rush);box-shadow:0 0 0 3px rgba(225,6,0,.18)}
.stage.play .dash{animation:dashIn 1s cubic-bezier(.2,.7,.2,1) 4.05s both}
@keyframes dashIn{0%{opacity:0;transform:translateY(-50%) translateX(44px)}100%{opacity:1;transform:translateY(-50%) translateX(0)}}
.stage.settled .dash{opacity:1;transform:translateY(-50%);animation:none}

.tag-block{position:absolute;left:clamp(1.5rem,6vw,5rem);bottom:19vh;z-index:12;
  max-width:min(42rem,58vw);opacity:0}
.tag{font-family:var(--f-display);font-weight:700;
  font-size:min(clamp(3rem,9vw,8.2rem),13.5vh);line-height:.84;
  letter-spacing:-.022em;text-transform:uppercase;color:var(--text);
  text-shadow:0 2px 40px rgba(0,0,0,.55)}
.tag span{display:block;color:var(--steel-dim)}
.tag .p{color:var(--text);position:relative;width:max-content}
.tag .p::after{content:"";position:absolute;left:.04em;bottom:-.16em;width:2.8rem;height:.11em;background:var(--rush)}
.tag-sub{margin-top:1.5rem;font-family:var(--f-body);font-weight:400;
  font-size:clamp(1rem,1.5vw,1.24rem);line-height:1.55;letter-spacing:.01em;
  color:var(--text-dim);max-width:42ch;border-left:2px solid var(--rush);padding-left:15px}
.tag-cta{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.6rem;
  font-family:var(--f-display);font-weight:700;font-size:.86rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--text);text-decoration:none;padding:.72rem 1.3rem;
  background:linear-gradient(90deg,var(--rush),#ff3b2f);
  clip-path:polygon(8px 0,100% 0,100% calc(100% - 8px),calc(100% - 8px) 100%,0 100%,0 8px);
  transition:filter .15s ease,transform .15s ease}
.tag-cta:hover{filter:brightness(1.1);transform:translateX(2px)}
.tag-cta i{width:.5rem;height:.5rem;border-right:2px solid currentColor;border-bottom:2px solid currentColor;transform:rotate(-45deg)}
.tag-cta2{display:inline-block;margin:1.6rem 0 0 .9rem;font-family:var(--f-mono);font-size:.72rem;
  letter-spacing:.1em;text-transform:uppercase;color:var(--text-dim);text-decoration:none;
  border-bottom:1px solid var(--info);padding-bottom:2px;transition:color .15s ease,border-color .15s ease}
.tag-cta2:hover{color:var(--text);border-color:var(--text)}
@media(max-width:580px){.tag-cta2{display:block;margin:1rem 0 0}}
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
@media(max-width:580px){
  /* geri sayım gizlenmez — üstte kompakt tam-genişlik şerit; küçük dial + rakamlar yan yana */
  .dash{position:absolute;left:1rem;right:1rem;top:.85rem;width:auto;transform:none;
    padding:12px 14px 11px;z-index:14;opacity:1;
    display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto auto;
    column-gap:14px;align-items:center}
  .dash-hd{grid-column:1/-1}
  .dial{width:96px;height:96px;margin:4px 0;grid-row:2/4}
  .dial .ctr b{font-size:1.9rem}
  .dial .ctr s{font-size:.5rem}
  .dash-row{grid-column:2;justify-content:flex-start;gap:16px;margin:0}
  .dash-row b{font-size:1.35rem}
  .dash-row s{font-size:.46rem}
  .dash-ft{grid-column:2;margin:4px 0 0;padding:0;border:0;font-size:.5rem}
  .stage.play .dash,.stage.settled .dash{animation:none;opacity:1;transform:none}
  /* dar ekranda geri sayım kartı + başlık üst üste biniyordu: başlığı küçült,
     alt boşluğu azalt, blok kartın altında net başlasın */
  .tag-block{bottom:7vh;max-width:90vw}
  .tag{font-size:2rem;line-height:.94}
  .tag .p::after{width:2rem;bottom:-.1em}
  .tag-sub{margin-top:.85rem;font-size:.9rem;line-height:1.42;padding-left:11px;max-width:34ch}
  .tag-cta{margin-top:1rem;padding:.62rem 1.05rem}
  .tag-cta2{font-size:.68rem}
}
@media(prefers-reduced-motion:reduce){
  .stage.play .wordmark,.stage.play .dash,.stage.play .bloom,.stage.play .tag-block,.stage.play .circuit,.bloom{animation:none!important}
  .wordmark{display:none}
  .dash{opacity:1!important;transform:translateY(-50%)!important}.bloom{opacity:.7!important}
  .tag-block{opacity:1!important;transform:none!important}
  .circuit{opacity:.62!important}.circuit .draw{stroke-dashoffset:0}
  .circuit .pulse,.circuit .pulse2,.datastream{display:none}
  .skip{display:none}
}
</style></head>
<body>
<main class="stage" id="stage">
  <canvas id="smoke"></canvas>
  <div class="bloom"></div>
  <div class="telemetry">
    <svg class="circuit" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true"
         xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
      <defs>
        <path id="fpTrack" d="M240 720C240 560 300 470 430 470L820 470C940 470 980 400 980 320C980 230 910 190 820 190L560 190C470 190 450 120 530 95C640 62 820 78 1010 78L1240 78C1400 78 1480 180 1480 350C1480 500 1390 560 1250 578L1030 600C950 608 935 665 1000 700C1075 740 1230 726 1330 758C1440 792 1450 862 1320 862L420 862C290 862 240 800 240 720Z"/>
        <filter id="fpGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g class="grid">
        <line x1="0" y1="180" x2="1600" y2="180"/><line x1="0" y1="360" x2="1600" y2="360"/>
        <line x1="0" y1="540" x2="1600" y2="540"/><line x1="0" y1="720" x2="1600" y2="720"/>
        <line x1="320" y1="0" x2="320" y2="900"/><line x1="640" y1="0" x2="640" y2="900"/>
        <line x1="960" y1="0" x2="960" y2="900"/><line x1="1280" y1="0" x2="1280" y2="900"/>
      </g>
      <use xlink:href="#fpTrack" class="trk ghost"/>
      <use xlink:href="#fpTrack" class="trk draw"/>
      <use xlink:href="#fpTrack" class="trk pulse"/>
      <use xlink:href="#fpTrack" class="trk pulse2"/>
      <line class="sf" x1="240" y1="700" x2="240" y2="740"/>
      <g filter="url(#fpGlow)"><circle r="9" fill="#a9fbef">
        <animateMotion dur="13s" repeatCount="indefinite" rotate="auto"><mpath xlink:href="#fpTrack"/></animateMotion>
      </circle></g>
      <g filter="url(#fpGlow)"><circle r="8" fill="#ff6a5d">
        <animateMotion dur="17s" begin="-4s" repeatCount="indefinite" rotate="auto"><mpath xlink:href="#fpTrack"/></animateMotion>
      </circle></g>
    </svg>
    <div class="datastream ds1"><span>__DS1__</span><span>__DS1__</span></div>
    <div class="datastream ds2"><span>__DS2__</span><span>__DS2__</span></div>
  </div>
  <div class="wordmark">
    <svg viewBox="0 0 48 48"><path d="M13 11 L27 24 L13 37" fill="none" stroke="#e10600" stroke-width="6.5" stroke-linecap="square"/><path d="M24.5 15 L33.5 24 L24.5 33" fill="none" stroke="#e10600" stroke-width="5" stroke-linecap="square" opacity=".5"/></svg>
    FORMULA&nbsp;<b>PADDOCK</b>
  </div>
  <div class="scrim"></div>
  <div class="tag-block">
    <h1 class="tag"><span>Veriyle</span><span>konuşur.</span><span class="p">Uydurmaz.</span></h1>
    <p class="tag-sub">__SUB__</p>
    <a class="tag-cta" href="?p=live" target="_top">Sıradaki seansı izle<i></i></a>
    <a class="tag-cta2" href="?p=learn" target="_top">F1'e yeni misin? 2 dakikada başla →</a>
  </div>
  <aside class="dash __LIVECLS__" aria-label="Sıradaki seans">
    <div class="dash-hd"><span>SIRADAKI SEANS</span><b>__EVENT__</b></div>
    <div class="dial">
      <svg viewBox="0 0 200 200">
        <circle class="track" cx="100" cy="100" r="92"/>
        <circle class="tick" cx="100" cy="100" r="92" stroke-dasharray="1.4 12.6"/>
        <circle class="arcR" cx="100" cy="100" r="92"/>
        <circle class="arcA" cx="100" cy="100" r="74"/>
        <circle class="arcC" cx="100" cy="100" r="58"/>
      </svg>
      <div class="ctr"><b id="d">--</b><s>GÜN</s></div>
    </div>
    <div class="dash-row">
      <div><b id="h">--</b><s>SAAT</s></div>
      <div><b id="m">--</b><s>DK</s></div>
      <div><b id="s">--</b><s>SN</s></div>
    </div>
    <div class="dash-ft"><span class="lv"></span>__FOOT__</div>
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

/* CTA linkleri: iframe sandbox top-navigation'a izin vermiyor -> üst penceredeki
   görünmez nav butonuna tıkla (üst bardaki mekanizmanın aynısı). */
Array.prototype.forEach.call(document.querySelectorAll('.tag-cta,.tag-cta2'),function(a){
  a.addEventListener('click',function(e){
    var k=(a.getAttribute('href')||'').split('p=')[1]; if(!k) return;
    try{
      var b=window.parent.document.querySelector('[class*="st-key-njp_'+k+'"] button');
      if(b){ e.preventDefault(); b.click(); return; }
    }catch(err){}
    try{ e.preventDefault(); window.top.location.href='?p='+k; }catch(err){}
  });
});

/* geri sayım + pano halkaları */
function pad(n){return String(n).padStart(2,'0');}
function arc(sel,r,rem){
  var c=2*Math.PI*r, el=document.querySelector(sel);
  if(!el)return;
  el.setAttribute('stroke-dasharray',c.toFixed(1));
  el.setAttribute('stroke-dashoffset',(c*(1-Math.max(0,Math.min(1,rem)))).toFixed(1));
}
function cd(){
  var ms=Math.max(0,TARGET-Date.now());
  var d=Math.floor(ms/864e5),h=Math.floor(ms/36e5)%24,m=Math.floor(ms/6e4)%60,s=Math.floor(ms/1e3)%60;
  document.getElementById('d').textContent=pad(d);
  document.getElementById('h').textContent=pad(h);
  document.getElementById('m').textContent=pad(m);
  document.getElementById('s').textContent=pad(s);
  arc('.arcR',92,Math.min(d,14)/14);
  arc('.arcA',74,h/24);
  arc('.arcC',58,m/60);
}
if(TARGET>0){cd();setInterval(cd,1000);}
else{arc('.arcR',92,0);arc('.arcA',74,0);arc('.arcC',58,0);}

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
  setTimeout(function(){stage.classList.add('settled');},6200);
}
if(reduce||seen){settle();}else{requestAnimationFrame(play);}
setTimeout(function(){if(!stage.classList.contains('play'))settle();},3500);
document.getElementById('skip').addEventListener('click',settle);

/* hero tam-genişlik bir iframe: üzerindeki tekerlek olayı burada kalır ve
   ana sayfa kaymaz. Delta'yı ebeveynin kaydırma konteynerine ilet. */
try{
  var _host=null;
  try{ _host=window.parent.document.querySelector('[data-testid="stMain"]'); }catch(e){}
  if(_host){
    addEventListener('wheel',function(e){
      try{ _host.scrollBy({top:e.deltaY,left:0,behavior:'auto'}); }catch(_){}
    },{passive:true});
  }
}catch(e){}
})();
</script>
</body></html>"""


_DS1 = ("LAP 12/53   S1 28.441   S2 25.902   S3 24.115   SPD 337 KM/S   "
        "DRS ACIK   ERS 84%   FUEL 41.2 KG")
_DS2 = ("THR 100%   BRK 0%   GEAR 8   RPM 11 450   TYRE C4 SOFT L14   "
        "TRK 41 C   AIR 27 C   RUZGAR 1.2 M/S")


def _fmt_foot(is_live):
    return "canlı yayında" if is_live else "seans saati doğrulandı · TSİ"


def render(event_name, session_name, target_dt, is_live, height=760, subtitle=None):
    """Hero splash'i gömer. ``target_dt`` UTC datetime (veya None).

    ``subtitle`` verilmezse ``_DEFAULT_SUB`` kullanılır.
    """
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
        .replace("__SUB__", _ui.safe_html(subtitle or _DEFAULT_SUB))
        .replace("__DS1__", _ui.safe_html(_DS1))
        .replace("__DS2__", _ui.safe_html(_DS2))
        .replace("__FOOT__", _ui.safe_html(_fmt_foot(is_live)))
    )
    # tam-genişlik işareti: üst bar CSS'i bu işareti izleyen konteyneri
    # ekran kenarına yayar (Streamlit block-container padding'ini iptal eder).
    st.markdown('<div class="fp-hero-mark"></div>', unsafe_allow_html=True)
    components.html(html, height=height, scrolling=False)
