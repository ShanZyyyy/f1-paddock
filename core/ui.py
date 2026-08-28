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


_DOCK_STYLE = """
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
<div id="fp-dock">
  <div id="fp-dock-panel">
    <div class="fp-dock-row"><span>Görünüm</span>
      <div class="fp-dock-seg">
        <button data-theme="dark" id="fp-th-dark">Koyu</button>
        <button data-theme="light" id="fp-th-light">Açık</button>
      </div>
    </div>
    <div class="fp-dock-row"><span>Ortam müziği</span>
      <div class="fp-dock-music">
        <button id="fp-music">▶</button>
        <input id="fp-vol" type="range" min="0" max="100" value="35" aria-label="Ses">
      </div>
    </div>
  </div>
  <button id="fp-dock-toggle" aria-label="Ayarlar">⚙</button>
</div>
"""

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

  /* --- ortam muzigi: parent'a bagli AudioContext, iframe yeniden yuklense de yasar --- */
  var AC = (P.AudioContext || P.webkitAudioContext || window.AudioContext || window.webkitAudioContext);
  P.__fpAudio = P.__fpAudio || {ctx:null, master:null, nodes:[], on:false, vol:0.35};
  var A = P.__fpAudio;
  var mBtn = D.getElementById('fp-music'), vol = D.getElementById('fp-vol');

  function gain(){ return Math.max(0, Math.min(1, A.vol)) * 0.42; }
  function sync(){ if(mBtn) mBtn.textContent = A.on ? '⏸' : '▶'; if(vol) vol.value = Math.round(A.vol*100); }

  function startMusic(){
    if(!AC){ if(mBtn) mBtn.textContent='—'; return; }
    A.ctx = new AC();
    A.ctx.resume && A.ctx.resume();
    A.master = A.ctx.createGain(); A.master.gain.value = 0.0001; A.master.connect(A.ctx.destination);
    A.master.gain.setTargetAtTime(gain(), A.ctx.currentTime, 1.2);           /* yumusak giris */
    var filt = A.ctx.createBiquadFilter(); filt.type='lowpass'; filt.frequency.value=900; filt.Q.value=0.4; filt.connect(A.master);
    var chord = [110.0, 164.81, 220.0, 277.18];   /* A - E - A - C#  yumusak pad */
    chord.forEach(function(f,i){
      var o=A.ctx.createOscillator(); o.type = i===1 ? 'triangle' : 'sine'; o.frequency.value=f;
      var g=A.ctx.createGain(); g.gain.value=0.0001;
      o.connect(g); g.connect(filt); o.start();
      g.gain.setTargetAtTime(0.5/(i+1.6), A.ctx.currentTime, 4.0);
      var lfo=A.ctx.createOscillator(); lfo.frequency.value = 0.04 + i*0.02;
      var lg=A.ctx.createGain(); lg.gain.value = 2.5; lfo.connect(lg); lg.connect(o.detune); lfo.start();
      A.nodes.push(o, lfo);
    });
    A.on = true; sync();
  }
  function stopMusic(){
    A.nodes.forEach(function(n){ try{ n.stop(); }catch(e){} });
    A.nodes = []; if(A.ctx){ try{ A.ctx.close(); }catch(e){} } A.ctx=null; A.master=null;
    A.on = false; sync();
  }
  if(mBtn) mBtn.onclick=function(){ A.on ? stopMusic() : startMusic(); };
  if(vol) vol.oninput=function(e){ A.vol = e.target.value/100; if(A.master && A.ctx) A.master.gain.setTargetAtTime(gain(), A.ctx.currentTime, 0.1); };
  sync();
})();
</script>
"""


def control_dock():
    """Sag-alt yuzen kontrol panosu: koyu/acik tema + ortam muzigi."""
    st.markdown(_DOCK_STYLE, unsafe_allow_html=True)
    components.html(_DOCK_SCRIPT, height=0)


def inject_shell_theme():
    """Faz 2 gecis donemi kabuk temasi: :root jetonlari + yeni sakin arka plan
    + slim-rail sidebar (menu + expander'lar).

    Sayfa govde CSS'i henuz eski monolitten geldigi icin _COMPONENT_CSS'i
    enjekte etmiyoruz. Dosyanin EN SONUNDA cagrilmali ki eski !important
    bloklarini yensin.
    """
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(theme.shell_style(), unsafe_allow_html=True)


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


def news_card(title, source="", date="", excerpt="", link="", image=""):
    """Tek haber karti. link/image cagiran tarafta safe_external_url'den gecmeli."""
    meta = " · ".join(p for p in [source, date] if p)
    if image:
        ph = f"<div class='ph'><img src='{_html.escape(image, quote=True)}' alt='' onerror=\"this.parentElement.textContent='F1'\"></div>"
    else:
        ph = "<div class='ph'>F1</div>"
    lk = (f"<a class='lk' href='{_html.escape(link, quote=True)}' target='_blank' rel='noopener noreferrer'>"
          f"Haberi Ac &#8599;</a>") if link else ""
    st.markdown(
        f"<div class='fp-news'>{ph}<div class='bd'>"
        f"<span class='src'>{_esc(meta)}</span>"
        f"<div class='hl'>{_esc(title)}</div>"
        f"<div class='ex'>{_esc(excerpt)}</div>{lk}</div></div>",
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
    if hasattr(st, "iframe"):
        return st.iframe(document, height=height)
    return components.html(document, height=height, scrolling=scrolling)


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
        use_container_width=True,
        type="primary" if is_active else "secondary",
    )
