# -*- coding: utf-8 -*-
"""Formula Paddock — yeniden kullanilabilir arayuz bilesenleri.

Sayfalar ham HTML/``<style>`` yazmaz; bu fonksiyonlari cagirir.
Tum gorsel dil `core/theme.py`'den gelir.
"""

import html as _html
import json as _json
import re as _re

import streamlit as st
import streamlit.components.v1 as components

from core import theme

_LIGHT_KEY = "paddock_light_mode_v31"  # eski anahtarla uyumlu

_ACCENTS = {
    "red": "var(--fp-red)", "cyan": "var(--fp-cyan)", "amber": "var(--fp-amber)",
    "green": "var(--fp-green)", "purple": "var(--fp-purple)", "pink": "var(--fp-pink)",
}

_LINE_SEP = chr(0x2028)  # JS string icinde gecersiz
_PARA_SEP = chr(0x2029)


def _esc(value):
    return _html.escape(str(value if value is not None else ""))


def safe_html(value, *, quote=False):
    """Dış/kullanıcı verisini `unsafe_allow_html=True` markup'ına gömmeden önce
    kaçış uygula. KURAL: `st.markdown(f"<div>...{X}...", unsafe_allow_html=True)`
    içindeki HER dinamik değer (pilot/takım adı, haber başlığı, arama metni,
    API alanı) `safe_html(X)`'ten geçmeli. `quote=True` -> attribute değeri."""
    return _html.escape(str(value if value is not None else ""), quote=quote)


def _embed_html(markup, height=0, scrolling=False):
    """Tek HTML/JS gomme kapisi.

    Gorunur icerik -> `st.iframe` (yeni API, deprecate degil).
    height<=0 olan yalniz-JS yan etkileri (dock scripti, scroll_to) sifir
    ayak izi gerektirdiginden `components.html`'te kalir — `st.iframe` 0
    yuksekligi kabul etmiyor."""
    if height and height > 0 and hasattr(st, "iframe"):
        return st.iframe(markup, height=height)
    return components.html(markup, height=height, scrolling=scrolling)


def _accent(name):
    if name in _ACCENTS:
        return _ACCENTS[name]
    return name if str(name).startswith(("#", "var(")) else "var(--fp-red)"


def is_light():
    return bool(st.session_state.get(_LIGHT_KEY, False))


# =====================================================================
# TEMA ENJEKSIYONU — her sayfada bir kez, en ustte
# =====================================================================
def inject_theme():
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(theme.page_style(), unsafe_allow_html=True)
    st.markdown(theme.sidebar_style(), unsafe_allow_html=True)


_DOCK_CSS = """
<style>
#fp-dock{position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;flex-direction:column;
  align-items:flex-end;gap:10px;font-family:var(--fp-f-body)}
#fp-dock-toggle{width:46px;height:46px;border-radius:50%;border:1px solid var(--fp-line);
  background:linear-gradient(160deg,var(--fp-bg-3),var(--fp-bg-2));color:var(--fp-text);
  font-size:19px;cursor:pointer;box-shadow:var(--fp-shadow);display:flex;align-items:center;justify-content:center;
  transition:transform .15s ease,border-color .15s ease}
#fp-dock-toggle:hover{border-color:var(--fp-red);transform:rotate(35deg)}
#fp-dock-panel{display:none;flex-direction:column;gap:12px;width:232px;padding:14px;
  background:linear-gradient(160deg,var(--fp-bg-3),var(--fp-bg-2));border:1px solid var(--fp-line);
  border-radius:var(--fp-r-md);box-shadow:var(--fp-shadow)}
#fp-dock.open #fp-dock-panel{display:flex}
.fp-dock-row{display:flex;flex-direction:column;gap:6px}
.fp-dock-row > span{font:700 9.5px var(--fp-f-display);letter-spacing:.16em;text-transform:uppercase;color:var(--fp-text-mute)}
.fp-dock-seg{display:flex;gap:0;border:1px solid var(--fp-line);border-radius:var(--fp-r-sm);overflow:hidden}
.fp-dock-seg button{flex:1;padding:7px 4px;background:var(--fp-bg-2);border:0;color:var(--fp-text-dim);
  font:600 12px var(--fp-f-body);cursor:pointer}
.fp-dock-seg button.on{background:var(--fp-red);color:#fff}
.fp-dock-music{display:flex;align-items:center;gap:9px}
.fp-dock-music button{width:34px;height:34px;border-radius:var(--fp-r-sm);border:1px solid var(--fp-line);
  background:var(--fp-bg-2);color:var(--fp-text);font-size:13px;cursor:pointer;flex:0 0 auto}
.fp-dock-music input{flex:1;accent-color:var(--fp-red)}
@media(max-width:600px){#fp-dock{right:10px;bottom:10px}}
</style>
"""


def _dock_markup():
    from core import i18n as _i18n
    return (
        "<div id='fp-dock'><div id='fp-dock-panel'>"
        f"<div class='fp-dock-row'><span>{_esc(_i18n.t('dock.view'))}</span>"
        "<div class='fp-dock-seg'>"
        f"<button data-theme='dark' id='fp-th-dark'>{_esc(_i18n.t('dock.dark'))}</button>"
        f"<button data-theme='light' id='fp-th-light'>{_esc(_i18n.t('dock.light'))}</button>"
        "</div></div>"
        f"<div class='fp-dock-row'><span>{_esc(_i18n.t('dock.music'))}</span>"
        "<div class='fp-dock-music'><button id='fp-music'>&#9654;</button>"
        "<input id='fp-vol' type='range' min='0' max='100' value='35' aria-label='Ses'></div></div>"
        "</div><button id='fp-dock-toggle' aria-label='Ayarlar'>&#9881;</button></div>"
    )

_DOCK_SCRIPT = r"""
<script>
(function(){
  var P = window.parent, D = P.document, R = D.documentElement;
  var dock = D.getElementById('fp-dock');
  if(!dock) return;

  /* --- tema --- */
  function applyTheme(t){
    if(t==='light') R.setAttribute('data-fp-theme','light'); else R.removeAttribute('data-fp-theme');
    try{ P.localStorage.setItem('fp-theme', t); }catch(e){}
    var d=D.getElementById('fp-th-dark'), l=D.getElementById('fp-th-light');
    if(d) d.classList.toggle('on', t!=='light');
    if(l) l.classList.toggle('on', t==='light');
  }
  var saved='dark';
  try{ saved = P.localStorage.getItem('fp-theme')||'dark'; }catch(e){}
  applyTheme(saved);
  D.querySelectorAll('#fp-dock [data-theme]').forEach(function(b){
    b.onclick=function(){ applyTheme(b.getAttribute('data-theme')); };
  });

  /* --- dock ac/kapat --- */
  var tg = D.getElementById('fp-dock-toggle');
  if(tg) tg.onclick=function(){ dock.classList.toggle('open'); };

  /* --- ortam muzigi: parent'a bagli AudioContext, iframe yeniden yuklense de yasar ---
     Telifsiz, tamamen tarayicida uretilen lo-fi ambient yatak:
     akor ilerlemesi (Am7 - Fmaj7 - Cmaj7 - G) + yumusak bas + arpej + delay.  */
  var AC = (P.AudioContext || P.webkitAudioContext || window.AudioContext || window.webkitAudioContext);
  P.__fpAudio = P.__fpAudio || {ctx:null, master:null, timer:null, step:0, next:0, on:false, vol:0.35};
  var A = P.__fpAudio;
  var mBtn = D.getElementById('fp-music'), vol = D.getElementById('fp-vol');

  var PROG = [ [57,60,64,67], [53,57,60,65], [48,52,55,59], [55,59,62,67] ]; /* MIDI: Am7 Fmaj7 Cmaj7 G */
  function mtof(n){ return 440 * Math.pow(2, (n - 69) / 12); }
  function gain(){ return Math.max(0, Math.min(1, A.vol)) * 0.5; }
  function sync(){ if(mBtn) mBtn.textContent = A.on ? '⏸' : '▶'; if(vol) vol.value = Math.round(A.vol*100); }

  function startMusic(){
    if(!AC){ if(mBtn) mBtn.textContent='—'; return; }
    var ctx = new AC(); A.ctx = ctx; ctx.resume && ctx.resume();
    var master = ctx.createGain(); master.gain.value = 0.0001; A.master = master;
    var lp = ctx.createBiquadFilter(); lp.type='lowpass'; lp.frequency.value=1500; lp.Q.value=0.5;
    var dl = ctx.createDelay(1.0); dl.delayTime.value = 0.375;
    var fb = ctx.createGain(); fb.gain.value = 0.30;
    var wet = ctx.createGain(); wet.gain.value = 0.22;
    lp.connect(master); lp.connect(dl); dl.connect(fb); fb.connect(dl); dl.connect(wet); wet.connect(master);
    master.connect(ctx.destination);
    master.gain.setTargetAtTime(gain(), ctx.currentTime, 1.6);

    var tempo = 64, beat = 60/tempo, stepDur = beat/2;   /* 8'lik adimlar */
    A.step = 0; A.next = ctx.currentTime + 0.15;

    function voice(freq, t, dur, type, peak){
      if(!A.ctx) return;
      var o = ctx.createOscillator(); o.type = type || 'sine'; o.frequency.value = freq;
      var g = ctx.createGain(); g.gain.value = 0.0001;
      o.connect(g); g.connect(lp);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.start(t); o.stop(t + dur + 0.06);
    }

    function schedule(){
      if(!A.ctx) return;
      while(A.next < ctx.currentTime + 0.35){
        var s = A.step, chord = PROG[Math.floor(s/8) % PROG.length], inBar = s % 8;
        if(inBar === 0){
          chord.forEach(function(n,i){ voice(mtof(n), A.next, beat*4*1.05, i===0?'sine':'triangle', 0.05/(i*0.6+1)); });
        }
        if(inBar % 2 === 0){ voice(mtof(chord[0]-12), A.next, beat*0.95, 'sine', 0.13); }
        if(inBar !== 3 && inBar !== 6){
          var pool = chord.concat([chord[1]+12, chord[2]+12]);
          voice(mtof(pool[(s*3) % pool.length] + 12), A.next, stepDur*1.7, 'triangle', 0.032);
        }
        A.next += stepDur; A.step++;
      }
      A.timer = P.setTimeout(schedule, 70);
    }
    schedule();
    A.on = true; sync();
  }
  function stopMusic(){
    if(A.timer){ P.clearTimeout(A.timer); A.timer = null; }
    var c = A.ctx;
    if(A.master && c){ try{ A.master.gain.setTargetAtTime(0.0001, c.currentTime, 0.4); }catch(e){} }
    A.ctx = null; A.master = null; A.on = false;
    if(c){ setTimeout(function(){ try{ c.close(); }catch(e){} }, 900); }
    sync();
  }
  if(mBtn) mBtn.onclick=function(){ A.on ? stopMusic() : startMusic(); };
  if(vol) vol.oninput=function(e){ A.vol = e.target.value/100; if(A.master && A.ctx) A.master.gain.setTargetAtTime(gain(), A.ctx.currentTime, 0.1); };
  sync();
})();
</script>
"""


def control_dock():
    """Sag-alt yuzen kontrol panosu: koyu/acik tema + ortam muzigi."""
    st.markdown(_DOCK_CSS + _dock_markup(), unsafe_allow_html=True)
    _embed_html(_DOCK_SCRIPT, height=0)


def anchor(anchor_id):
    """Sayfaya gorunmez bir kaydirma hedefi koyar."""
    st.markdown(f"<div id='{_esc(anchor_id)}' style='scroll-margin-top:70px'></div>", unsafe_allow_html=True)


def scroll_to(anchor_id):
    """Verilen id'li ogeye yumusak kaydirir (rerun sonrasi).

    Streamlit rerun'da DOM birkac kare sonra oturdugundan tek setTimeout
    bazen erken calisiyor — elemani bulana kadar kisa araliklarla dener."""
    aid = str(anchor_id).replace("'", "")
    _embed_html(
        "<script>(function(){var n=0,d=window.parent.document,e=null;"
        "var t=setInterval(function(){"
        "e=e||d.getElementById('" + aid + "');"
        "if(e){e.scrollIntoView({behavior:(n>1?'smooth':'auto'),block:'start'});}"
        "if(++n>8){clearInterval(t);}},80);})();</script>",
        height=0,
    )


def inject_shell_theme():
    """Faz 2 gecis donemi kabuk temasi: :root jetonlari + yeni sakin arka plan
    + slim-rail sidebar (menu + expander'lar).

    Sayfa govde CSS'i henuz eski monolitten geldigi icin _COMPONENT_CSS'i
    enjekte etmiyoruz. Dosyanin EN SONUNDA cagrilmali ki eski !important
    bloklarini yensin.
    """
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(theme.shell_style(), unsafe_allow_html=True)
    # Eski "kendini çizen pist" arka planı kaldırıldı — sade koyu zemin +
    # ana sayfadaki hero duman'ı yeter. background_fx() artık çağrılmıyor.


_TOPBAR_SKELETON = r"""
/* Kritik önyükleme: sidebar/toolbar gizle + üst bar için boşluk. EN BAŞTA
   çalışır ki Streamlit tam temayı basmadan önce yerleşim doğru olsun. */
section[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],#MainMenu{
  display:none !important}
[data-testid="stHeader"],header[data-testid="stHeader"]{height:0 !important;min-height:0 !important;background:transparent !important}
.stApp [data-testid="stMain"] .block-container{padding-top:5.8rem}

/* ---- iskelet üst bar ----------------------------------------------------
   Gerçek .fp-tb gelene kadar (Streamlit yeniden yüklenirken) ekranda AYNI
   görünümde sabit bir bar durur — "menüsüz sayfaya gittim" hissi olmaz.
   Gerçek bar DOM'a girer girmez `body:has(.fp-tb)` ile gizlenir. */
[data-testid="stElementContainer"]:has(.fp-tb-skel){
  position:static !important;height:0 !important;min-height:0 !important;margin:0 !important;padding:0 !important;overflow:visible !important}
.fp-tb-skel{position:fixed;inset:0 0 auto 0;z-index:999990;
  display:flex;align-items:center;gap:clamp(.7rem,2vw,1.6rem);height:60px;
  padding:0 clamp(.9rem,3.5vw,2.4rem);font-family:var(--fp-f-body);
  background:linear-gradient(180deg,rgba(8,10,15,.985),rgba(8,10,15,.95));
  border-bottom:1px solid var(--fp-line);
  -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}
body:has(.fp-tb) .fp-tb-skel{display:none}
.fp-tb-skel a{text-decoration:none;color:inherit}
.fp-tb-skel .b{display:flex;align-items:center;gap:.45rem;flex:0 0 auto;white-space:nowrap;
  font-family:var(--fp-f-display);font-weight:800;font-size:15px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--fp-text)}
.fp-tb-skel .b svg{width:14px;height:14px;flex:0 0 auto}
.fp-tb-skel .b i{color:var(--fp-red);font-style:normal}
.fp-tb-skel nav{display:flex;align-items:center;gap:clamp(.55rem,1.6vw,1.35rem);flex:1 1 auto;min-width:0;overflow:hidden}
.fp-tb-skel nav a{font:600 11px/1 var(--fp-f-display);letter-spacing:.13em;text-transform:uppercase;
  color:var(--fp-text-dim);white-space:nowrap;padding:22px 1px;border-bottom:2px solid transparent}
.fp-tb-skel .lang{margin-left:auto;flex:0 0 auto;display:flex;border:1px solid var(--fp-line);border-radius:3px;overflow:hidden}
.fp-tb-skel .lang span{padding:4px 8px;font:600 10px var(--fp-f-mono);letter-spacing:.1em;color:var(--fp-text-mute)}
.fp-tb-skel .lang .on{background:var(--fp-red);color:#fff}
@media(max-width:1080px){.fp-tb-skel .lang{display:none}}
@media(max-width:940px){.fp-tb-skel nav{-webkit-mask-image:linear-gradient(90deg,#000 92%,transparent);mask-image:linear-gradient(90deg,#000 92%,transparent)}}
@media(max-width:620px){.fp-tb-skel .b{font-size:0;letter-spacing:0}.fp-tb-skel .b svg{width:22px;height:22px}}
"""

# İskelet barın linkleri gerçek NAV birincil sayfalarıyla eşleşir; TR etiketleri
# sabit (baskın dil) — 200 ms'lik yer tutucu, gerçek bar hemen üstüne biner.
_TOPBAR_SKELETON_HTML = (
    "<div class='fp-tb-skel' aria-hidden='true'>"
    "<span class='b'>"
    "<svg viewBox='0 0 48 48'><path d='M13 11 L27 24 L13 37' fill='none' stroke='#e10600' stroke-width='6.5' stroke-linecap='square'/>"
    "<path d='M24.5 15 L33.5 24 L24.5 33' fill='none' stroke='#e10600' stroke-width='5' stroke-linecap='square' opacity='.5'/></svg>"
    "Formula&nbsp;<i>Paddock</i></span>"
    "<nav>"
    "<a href='?p=news' target='_self'>Haber Merkezi</a>"
    "<a href='?p=telemetry' target='_self'>Veri &amp; Analiz</a>"
    "<a href='?p=live' target='_self'>Canlı &amp; Yarış</a>"
    "<a href='?p=learn' target='_self'>Paddock</a>"
    "<a href='?p=teams' target='_self'>Şampiyonalar</a>"
    "<a href='?p=games' target='_self'>Oyunlar</a>"
    "</nav>"
    "<span class='lang'><span class='on'>TR</span><span>EN</span></span>"
    "</div>"
)


def inject_rail_theme():
    """Kritik önyükleme CSS'i — EN BAŞTA (set_page_config'ten hemen sonra):
    :root jetonları + sidebar/toolbar'ı gizle + üst bar için boşluk +
    gerçek bar gelene kadar duracak iskelet bar."""
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(
        "<style>" + theme._root_vars_dual() + _TOPBAR_SKELETON + "</style>"
        + _TOPBAR_SKELETON_HTML,
        unsafe_allow_html=True,
    )


def background_fx():
    """Sayfanin en arkasina canli pist arka planini (#fp-bgfx) enjekte eder.
    Kendini cizen tur + iki arac (SMIL animateMotion). CSS'i shell_style icinde."""
    st.markdown(theme._BG_FX_HTML, unsafe_allow_html=True)


inject_sidebar_theme = inject_shell_theme  # geriye donuk ad


# =====================================================================
# BASLIKLAR
# =====================================================================
def page_header(title, subtitle="", eyebrow="", badge=None, badge_tone="neutral"):
    badge_html = ""
    if badge:
        cls = {"live": "live", "wait": "wait"}.get(badge_tone, "")
        badge_html = f"<span class='fp-badge {cls}'>{_esc(badge)}</span>"
    eyebrow_html = f"<div class='fp-eyebrow'>{_esc(eyebrow)}</div>" if eyebrow else ""
    sub_html = f"<div class='sub'>{_esc(subtitle)}</div>" if subtitle else ""
    st.markdown(
        f"<div class='fp-page-header'><div>{eyebrow_html}"
        f"<h1>{_esc(title)}</h1>{sub_html}</div>{badge_html}</div>",
        unsafe_allow_html=True,
    )


def section_title(text):
    st.markdown(f"<div class='fp-section'>{_esc(text)}</div>", unsafe_allow_html=True)


# =====================================================================
# KARTLAR
# =====================================================================
def hud_card(label, value, copy="", accent="red"):
    copy_html = f"<div class='cpy'>{_esc(copy)}</div>" if copy else ""
    st.markdown(
        f"<div class='fp-hud' style='--accent:{_accent(accent)}'>"
        f"<div class='lbl'>{_esc(label)}</div><div class='val'>{_esc(value)}</div>{copy_html}</div>",
        unsafe_allow_html=True,
    )


def stat_tile(label, value, sub="", accent="cyan", mono=True):
    """mono=True: buyuk JetBrains Mono (sayi/zaman icin).
    mono=False: kompakt Saira (metin degerleri icin — takim adi, durum vb.)."""
    sub_html = f"<div class='sub'>{_esc(sub)}</div>" if sub else ""
    val_cls = "val" if mono else "val txt"
    st.markdown(
        f"<div class='fp-tile' style='--accent:{_accent(accent)}'>"
        f"<div class='lbl'>{_esc(label)}</div><div class='{val_cls}'>{_esc(value)}</div>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def mini_note(text, accent="cyan"):
    st.markdown(
        f"<div class='fp-note' style='--nc:{_accent(accent)}'>{_esc(text)}</div>",
        unsafe_allow_html=True,
    )


def pit_grid(items):
    """Pit-duvarı telemetri paneli: yan yana değer widget'ları (`.fp-hud`
    kartları) tek CSS grid'inde — `st.columns`'un eşitsiz kart sorunu yok.

    ``items`` = [{label, value, copy?, accent?}, ...]  (accent: red/cyan/amber/green/pink)
    """
    cards = [i for i in items if str((i or {}).get("value", "")).strip() or str((i or {}).get("label", "")).strip()]
    if not cards:
        return
    cells = []
    for c in cards:
        copy = str(c.get("copy", "") or "")
        copy_html = f"<div class='cpy'>{_esc(copy)}</div>" if copy else ""
        cells.append(
            f"<div class='fp-hud' style='--accent:{_accent(c.get('accent', 'cyan'))}'>"
            f"<div class='lbl'>{_esc(c.get('label', ''))}</div>"
            f"<div class='val'>{_esc(c.get('value', ''))}</div>{copy_html}</div>"
        )
    st.markdown(f"<div class='fp-pit'>{''.join(cells)}</div>", unsafe_allow_html=True)


def notes_grid(items, per_row=3):
    """Birden çok mini notu TEK CSS grid'inde çizer — hepsi eşit yükseklik,
    st.columns'un eşitsiz kart sorunu olmadan."""
    items = [i for i in items if str(i).strip()]
    if not items:
        return
    tones = ("cyan", "amber", "pink", "purple", "green")
    cells = "".join(
        f"<div class='fp-note' style='--nc:{_accent(tones[i % len(tones)])}'>{_esc(t)}</div>"
        for i, t in enumerate(items)
    )
    st.markdown(
        f"<div class='fp-notes-grid' style='--per:{int(per_row)}'>{cells}</div>",
        unsafe_allow_html=True,
    )


def _news_card_markup(title, source="", date="", excerpt="", link="", image=""):
    meta = " · ".join(p for p in [source, date] if p)
    if image:
        ph = f"<div class='ph'><img src='{_html.escape(image, quote=True)}' alt='' onerror=\"this.parentElement.textContent='F1'\"></div>"
    else:
        ph = "<div class='ph'>F1</div>"
    lk = (f"<a class='lk' href='{_html.escape(link, quote=True)}' target='_blank' rel='noopener noreferrer'>"
          f"Haberi Ac &#8599;</a>") if link else ""
    return (
        f"<div class='fp-news'>{ph}<div class='bd'>"
        f"<span class='src'>{_esc(meta)}</span>"
        f"<div class='hl'>{_esc(title)}</div>"
        f"<div class='ex'>{_esc(excerpt)}</div>{lk}</div></div>"
    )


def news_card(title, source="", date="", excerpt="", link="", image=""):
    """Tek haber karti. link/image cagiran tarafta safe_external_url'den gecmeli."""
    st.markdown(_news_card_markup(title, source, date, excerpt, link, image),
                unsafe_allow_html=True)


def news_grid(items, per_row=2):
    """Haber kartlarini TEK CSS grid'inde cizer — ayni satirdaki kartlar esit
    yukseklik olur (`st.columns`'un sutunlari ayri yiginladigi icin olusan
    esitsiz kart sorunu yok). `notes_grid` ile ayni desen.

    ``items`` = [{title, source, date, excerpt, link, image}, ...]
    link/image cagiran tarafta `safe_external_url`'den gecmeli."""
    cards = [i for i in items if str((i or {}).get("title", "")).strip()]
    if not cards:
        return
    cells = "".join(
        _news_card_markup(
            c.get("title", ""), c.get("source", ""), c.get("date", ""),
            c.get("excerpt", ""), c.get("link", ""), c.get("image", ""),
        )
        for c in cards
    )
    st.markdown(
        f"<div class='fp-news-grid' style='--per:{int(per_row)}'>{cells}</div>",
        unsafe_allow_html=True,
    )


def data_state(title, message, tone="info"):
    colour = theme.tone_hex(tone, is_light())
    st.markdown(
        f"<div class='fp-state' style='--sc:{colour}'>"
        f"<div class='st'>{_esc(title)}</div><div class='sc'>{_esc(message)}</div></div>",
        unsafe_allow_html=True,
    )


def result_hero(event, session_label, winner_name, team, gap_text, runners=None):
    """Yaris bitince: kim, hangi takim, kac saniye farkla kazandi.

    ``runners`` = [(pos_label, code, team), ...] — 2. ve 3. icin kisa satir.
    Hicbir alan uydurulmaz; veri yoksa cagiran taraf bu fonksiyonu cagirmaz.
    """
    colour = theme.team_color(team)
    eyebrow = " · ".join(p for p in [event, session_label, "Resmi Sonuc"] if p)
    parts = str(winner_name or "").split()
    if len(parts) > 1:
        name_html = f"{_esc(' '.join(parts[:-1]))} <b>{_esc(parts[-1])}</b>"
    else:
        name_html = f"<b>{_esc(winner_name)}</b>"
    gap_html = ""
    if gap_text:
        gap_html = f"<span class='gap'><span>KAZANMA FARKI</span>{_esc(gap_text)}</span>"
    next_html = ""
    if runners:
        bits = [f"{_esc(pos)} <b>{_esc(code)}</b> ({_esc(tm)})" for pos, code, tm in runners]
        next_html = "<span class='next'>" + " &nbsp;·&nbsp; ".join(bits) + "</span>"
    st.markdown(
        f"<div class='fp-result' style='--tc:{colour}'>"
        f"<div class='eb'>{_esc(eyebrow)}</div>"
        f"<div class='nm'>{name_html} kazandi</div>"
        f"<div class='row'><span class='team'>{_esc(team)}</span>{gap_html}{next_html}</div></div>",
        unsafe_allow_html=True,
    )


# =====================================================================
# IZOLE HUD IFRAME — tek guvenli render kapisi
# =====================================================================
def render_html_hud(markup, height=150, scrolling=False):
    if not isinstance(markup, str) or not markup.strip():
        st.info("Bu HUD icin gosterilecek dogrulanmis veri henuz yok.")
        return None
    theme_css = "<style>" + theme.hud_iframe_style(is_light()) + "</style>"
    document = markup.strip()
    if "<html" not in document.lower():
        document = (
            '<!doctype html><html><head><meta charset="utf-8">'
            + theme_css
            + '</head><body><div class="fp-hud-shell">'
            + document
            + "</div></body></html>"
        )
    elif "</head>" in document.lower():
        document = _re.sub(r"</head>", theme_css + "</head>", document, count=1, flags=_re.IGNORECASE)
    else:
        document = theme_css + document
    return _embed_html(document, height=height, scrolling=scrolling)


def json_for_script(obj, compact=True):
    """JSON'u satir ici <script> blenda guvenle gomer: </script>, yorum ve
    satir ayiricilarini escape'ler. compact=True -> bosluksuz (buyuk payload)."""
    seps = (",", ":") if compact else None
    text = _json.dumps(obj, ensure_ascii=False, separators=seps)
    text = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    text = text.replace(_LINE_SEP, "\\u2028").replace(_PARA_SEP, "\\u2029")
    return text


# =====================================================================
# SIDEBAR
# =====================================================================
def sidebar_brand():
    st.sidebar.markdown(
        "<div class='fp-brand'><span class='mark'></span>"
        "<span class='txt'>Formula&nbsp;Paddock<s>Race Intelligence</s></span></div>",
        unsafe_allow_html=True,
    )


def sidebar_section(label):
    st.sidebar.markdown(f"<div class='fp-nav-sec'>{_esc(label)}</div>", unsafe_allow_html=True)


def nav_button(label, icon, page_key, current_page):
    """Slim-rail nav satiri. Aktif sayfa 'primary' tipiyle isaretlenir.

    ``icon`` = Material sembol adi, orn. "home" -> :material/home:
    Tiklaninca True doner (cagiran taraf sayfayi degistirip rerun eder).
    """
    is_active = current_page == page_key
    return st.sidebar.button(
        label,
        key=f"nav_{page_key}",
        icon=f":material/{icon}:" if icon else None,
        width='stretch',
        type="primary" if is_active else "secondary",
    )


# =====================================================================
# ÜST BAR — hover'da aşağı sarkan mega-menü (sol menünün yerini alır)
# =====================================================================
_TOPBAR_CSS = r"""
<style>
section[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none !important}
[data-testid="stHeader"],header[data-testid="stHeader"]{background:transparent !important;
  height:0 !important;min-height:0 !important;pointer-events:none}
[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],#MainMenu{
  display:none !important}
.stApp [data-testid="stMain"] .block-container{padding-top:5.8rem;max-width:1200px}

/* açılış hero'su tam genişlik + tepeye kadar (fp-hero-mark işaretinden sonraki konteyner) */
[data-testid="stElementContainer"]:has(.fp-hero-mark){display:none !important}
[data-testid="stElementContainer"]:has(.fp-hero-mark) + [data-testid="stElementContainer"]{
  width:100vw !important;max-width:none !important;margin-left:calc(50% - 50vw) !important;
  height:100svh !important}
[data-testid="stElementContainer"]:has(.fp-hero-mark) + [data-testid="stElementContainer"] iframe{
  width:100vw !important;height:100svh !important;display:block}
.stApp [data-testid="stMain"] .block-container:has(.fp-hero-mark){
  padding:0 !important;max-width:none !important}
/* hero sayfasında dikey scroll'u kes — ana ekran = tam hero */
.stApp [data-testid="stMain"]:has(.fp-hero-mark){overflow:hidden !important}

.fp-tb{position:fixed;inset:0 0 auto 0;z-index:1000000;font-family:var(--fp-f-body)}
.fp-tb-bar{display:flex;align-items:center;gap:clamp(.7rem,2vw,1.6rem);height:60px;
  padding:0 clamp(.9rem,3.5vw,2.4rem);
  background:linear-gradient(180deg,rgba(8,10,15,.985),rgba(8,10,15,.95));
  border-bottom:1px solid var(--fp-line);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
.fp-tb a{text-decoration:none;color:inherit}
.fp-tb-brand{display:flex;align-items:center;gap:.45rem;font-family:var(--fp-f-display);font-weight:800;
  font-size:15px;letter-spacing:.11em;text-transform:uppercase;color:var(--fp-text);white-space:nowrap;
  margin-right:.3rem;flex:0 0 auto}
.fp-tb-brand .ch{width:14px;height:14px;flex:0 0 auto}
.fp-tb-brand b{color:var(--fp-red);font-weight:800}
.fp-tb-nav{display:flex;align-items:center;gap:clamp(.55rem,1.6vw,1.35rem);flex:1 1 auto;min-width:0}

/* sekme / grup başlığı */
.fp-tb-g{position:relative;font:600 11px/1 var(--fp-f-display);letter-spacing:.13em;text-transform:uppercase;
  color:var(--fp-text-dim);white-space:nowrap}
.fp-tb-g>a,.fp-tb-g>span{display:inline-flex;align-items:center;gap:.35em;padding:22px 1px;
  border-bottom:2px solid transparent;transition:color .15s ease,border-color .15s ease;cursor:pointer}
.fp-tb-g:hover>a,.fp-tb-g:hover>span,.fp-tb-g:focus-within>a,.fp-tb-g.on>a,.fp-tb-g.on>span{
  color:var(--fp-text);border-color:var(--fp-red)}
.fp-tb-g .caret{width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;
  transform:rotate(45deg) translateY(-1px);opacity:.55;transition:transform .18s ease}
.fp-tb-g:hover .caret{transform:rotate(45deg) translateY(1px)}

/* grup açılır listesi */
.fp-drop{position:absolute;top:56px;left:-14px;min-width:212px;padding:.5rem;
  background:linear-gradient(180deg,rgba(11,14,20,.98),rgba(8,10,14,.97));
  border:1px solid var(--fp-line);border-radius:5px;box-shadow:0 22px 44px rgba(0,0,0,.5);
  opacity:0;visibility:hidden;transform:translateY(-6px);transition:opacity .16s ease,transform .16s ease,visibility .16s;
  display:flex;flex-direction:column;gap:1px}
.fp-tb-g:last-child .fp-drop{left:auto;right:-14px}
.fp-tb-g:hover .fp-drop,.fp-tb-g:focus-within .fp-drop{opacity:1;visibility:visible;transform:translateY(0)}
.fp-drop::before{content:"";position:absolute;top:-12px;left:0;right:0;height:14px}   /* hover köprüsü */
.fp-drop a{font:500 13px/1.1 var(--fp-f-body);letter-spacing:.01em;color:var(--fp-text-dim);
  padding:9px 12px;border-radius:4px;border-left:2px solid transparent;text-transform:none;
  transition:background .12s ease,color .12s ease,border-color .12s ease}
.fp-drop a:hover{background:rgba(255,255,255,.06);color:var(--fp-text)}
.fp-drop a.on{color:var(--fp-text);border-left-color:var(--fp-red);
  background:linear-gradient(90deg,rgba(225,6,0,.16),transparent 72%)}

.fp-tb-right{margin-left:auto;display:flex;align-items:center;gap:1rem;white-space:nowrap}
.fp-tb-sesh{font:500 10.5px/1.2 var(--fp-f-mono);letter-spacing:.08em;color:var(--fp-text-mute);
  display:flex;align-items:center;gap:.5rem}
.fp-tb-sesh .lv{width:6px;height:6px;border-radius:50%;background:var(--fp-red)}
.fp-tb-sesh.live .lv{box-shadow:0 0 0 0 rgba(225,6,0,.5);animation:fp-tb-pulse 2s ease-out infinite}
@keyframes fp-tb-pulse{70%{box-shadow:0 0 0 7px rgba(225,6,0,0)}100%{box-shadow:0 0 0 0 rgba(225,6,0,0)}}
.fp-tb-lang{display:flex;border:1px solid var(--fp-line);border-radius:3px;overflow:hidden}
.fp-tb-lang a{padding:4px 8px;font:600 10px var(--fp-f-mono);letter-spacing:.1em;color:var(--fp-text-mute)}
.fp-tb-lang a.on{background:var(--fp-red);color:#fff}

@media(max-width:1080px){.fp-tb-sesh{display:none !important}}
@media(max-width:940px){
  .fp-tb-nav{overflow-x:auto;overflow-y:visible;-ms-overflow-style:none;scrollbar-width:none;
    -webkit-overflow-scrolling:touch;-webkit-mask-image:linear-gradient(90deg,#000 92%,transparent);
    mask-image:linear-gradient(90deg,#000 92%,transparent)}
  .fp-tb-nav::-webkit-scrollbar{display:none}
  .fp-drop,.fp-tb-g:last-child .fp-drop{position:fixed;left:0;right:0;top:60px;min-width:0;
    border-radius:0;border-left:0;border-right:0}
}
@media(max-width:620px){
  .fp-tb-brand{gap:0;font-size:0;letter-spacing:0;margin-right:.15rem}
  .fp-tb-brand .ch{width:22px;height:22px}
  .fp-tb-bar{gap:.7rem;padding:0 .8rem}
}
</style>
"""

_CHEVRON = ("<svg class='ch' viewBox='0 0 48 48'>"
            "<path d='M13 11 L27 24 L13 37' fill='none' stroke='%23e10600' stroke-width='6.5' stroke-linecap='square'/>"
            "<path d='M24.5 15 L33.5 24 L24.5 33' fill='none' stroke='%23e10600' stroke-width='5' stroke-linecap='square' opacity='.5'/>"
            "</svg>").replace("%23", "#")


_TOPBAR_ACTIVE_JS = """
<script>
(function(){
  var PW=window.parent, D;
  try{ D=PW.document; }catch(e){ return; }

  function paint(){
    try{
      var p=new URLSearchParams(PW.location.search).get('p')||'home';
      D.querySelectorAll('.fp-tb-g').forEach(function(g){
        var m=[].some.call(g.querySelectorAll('a'),function(a){return (a.getAttribute('href')||'')==='?p='+p;});
        g.classList.toggle('on',m);
      });
      D.querySelectorAll('.fp-drop a').forEach(function(a){
        a.classList.toggle('on',(a.getAttribute('href')||'')==='?p='+p);
      });
    }catch(e){}
  }

  // Gezinme sırasında barın "kaybolduğu" izlenimini azalt: tıklanır tıklanmaz
  // yeni sayfanın aktif işaretini boya (Streamlit yeniden yüklerken bile bar
  // en başta çizildiği için görünür kalır).
  function bind(){
    try{
      D.querySelectorAll('.fp-tb a[href^="?p="]').forEach(function(a){
        if(a.__fp) return; a.__fp=1;
        a.addEventListener('click',function(){
          var k=(a.getAttribute('href')||'').split('p=')[1];
          if(!k) return;
          D.querySelectorAll('.fp-tb-g').forEach(function(g){
            g.classList.toggle('on',[].some.call(g.querySelectorAll('a'),function(x){return (x.getAttribute('href')||'')==='?p='+k;}));
          });
        });
      });
    }catch(e){}
  }
  paint(); bind();
  setTimeout(function(){paint();bind();},60);
  setTimeout(function(){paint();bind();},300);
  PW.addEventListener('popstate',paint);
})();
</script>
"""


def topbar(current, lang, standalone=(), groups=(), session_line="", session_live=False):
    """Sabit üst bar. Düz sekmeler (``standalone``) + her biri kendi açılır
    listesi olan gruplar (``groups``). Sol menünün yerini alır.

    Aktif sayfa işareti sunucuda basılmaz; küçük bir istemci scripti URL'den
    okuyup uygular — böylece st.markdown çıktısı her rerun'da AYNI kalır ve
    Streamlit DOM'u yeniden kurmaz (bar sayfa geçişinde kaybolmaz/titremez).

    ``standalone`` = [(key, label), ...]  — doğrudan link, açılır liste yok
    ``groups``     = [(başlık, birincil_key, [(key, label), ...]), ...]
    """
    def _dl(key, label):
        return f"<a href='?p={_esc(key)}' target='_self'>{_esc(label)}</a>"

    def _plain(key, label):
        return f"<div class='fp-tb-g'><a href='?p={_esc(key)}' target='_self'>{_esc(label)}</a></div>"

    tabs = [_plain(k, lbl) for k, lbl in standalone]
    for title, primary, pages in groups:
        if len(pages) <= 1:                      # tek sayfalı grup = düz link
            k = pages[0][0] if pages else primary
            tabs.append(_plain(k, title))
            continue
        drop = "".join(_dl(k, lbl) for k, lbl in pages)
        tabs.append(
            "<div class='fp-tb-g'>"
            f"<a href='?p={_esc(primary)}' target='_self'>{_esc(title)}<i class='caret'></i></a>"
            f"<div class='fp-drop'>{drop}</div></div>"
        )

    lang_html = (
        "<div class='fp-tb-lang'>"
        f"<a class='{'on' if lang == 'tr' else ''}' href='?lang=tr' target='_self'>TR</a>"
        f"<a class='{'on' if lang == 'en' else ''}' href='?lang=en' target='_self'>EN</a>"
        "</div>"
    )
    sesh = ""
    if session_line:
        sesh = (f"<span class='fp-tb-sesh{' live' if session_live else ''}'>"
                f"<span class='lv'></span>{_esc(session_line)}</span>")

    st.markdown(
        _TOPBAR_CSS
        + "<div class='fp-tb'><div class='fp-tb-bar'>"
        + f"<a class='fp-tb-brand' href='?p=home' target='_self'>{_CHEVRON}Formula&nbsp;<b>Paddock</b></a>"
        + f"<nav class='fp-tb-nav'>{''.join(tabs)}</nav>"
        + f"<div class='fp-tb-right'>{sesh}{lang_html}</div>"
        + "</div></div>",
        unsafe_allow_html=True,
    )
    _embed_html(_TOPBAR_ACTIVE_JS, height=0)
