import React, { useState, useEffect, useRef, useMemo } from "react";
import "./hud.css";


/* ---------- ikon seti — ince hat, motor sporu ---------- */
const Ico = {
  replay: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4"/><path d="M11 9l5 3-5 3z" fill="currentColor" stroke="none"/></svg>,
  wall: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 10h18M9 10v10M15 4v6"/></svg>,
  tyre: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...p}><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.4"/><path d="M12 3v3.6M12 17.4V21M3 12h3.6M17.4 12H21M5.6 5.6l2.5 2.5M15.9 15.9l2.5 2.5M18.4 5.6l-2.5 2.5M8.1 15.9l-2.5 2.5" strokeLinecap="round"/></svg>,
  gauge: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 15a8 8 0 0 1 16 0"/><path d="M12 15l4-4"/><circle cx="12" cy="15" r="1.2" fill="currentColor" stroke="none"/></svg>,
  brake: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3"/></svg>,
  gear: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 7h10M4 7l3-3M4 7l3 3M20 17H10M20 17l-3-3M20 17l-3 3"/></svg>,
  drs: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 8h18M3 8l3 8h12l3-8M8 8v8M16 8v8"/></svg>,
  ers: (p) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M13 3 4 14h6l-1 7 9-11h-6z"/></svg>,
  play: (p) => <svg viewBox="0 0 24 24" fill="currentColor" {...p}><path d="M8 5v14l11-7z"/></svg>,
  pause: (p) => <svg viewBox="0 0 24 24" fill="currentColor" {...p}><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>,
};

/* ---------- veri ---------- */
const TEAM = {
  redbull:{n:"Red Bull",c:"var(--t-redbull)"}, ferrari:{n:"Ferrari",c:"var(--t-ferrari)"},
  mercedes:{n:"Mercedes",c:"var(--t-mercedes)"}, mclaren:{n:"McLaren",c:"var(--t-mclaren)"},
  aston:{n:"Aston Martin",c:"var(--t-aston)"}, williams:{n:"Williams",c:"var(--t-williams)"},
  rb:{n:"Racing Bulls",c:"var(--t-rb)"}, alpine:{n:"Alpine",c:"var(--t-alpine)"},
};
const CMP = { S:"var(--pink)", M:"var(--amber)", H:"#dfe6ee", I:"var(--green)" };

const GRID = [
  {p:1, code:"NOR", team:"mclaren", gap:"YARIŞ LİDERİ", int:"—", tyre:"H", age:12, d:"+0.31", up:true,
     sectors:["best","green","green"]},
  {p:2, code:"VER", team:"redbull", gap:"+2.4", int:"+2.4", tyre:"H", age:9, d:"+0.08", up:true,
     sectors:["green","best","green"], focus:true},
  {p:3, code:"LEC", team:"ferrari", gap:"+6.1", int:"+3.7", tyre:"M", age:22, d:"-0.19", up:false,
     sectors:["green","hold","green"]},
  {p:4, code:"PIA", team:"mclaren", gap:"+9.8", int:"+3.7", tyre:"H", age:12, d:"+0.12", up:true,
     sectors:["green","green","hold"]},
  {p:5, code:"RUS", team:"mercedes", gap:"+14.2", int:"+4.4", tyre:"M", age:20, d:"-0.26", up:false,
     sectors:["hold","green","hold"]},
  {p:6, code:"HAM", team:"ferrari", gap:"+18.9", int:"+4.7", tyre:"H", age:11, d:"+0.40", up:true,
     sectors:["green","green","green"]},
  {p:7, code:"ANT", team:"mercedes", gap:"+24.1", int:"+5.2", tyre:"H", age:10, d:"+0.05", up:true,
     sectors:["hold","green","green"]},
  {p:8, code:"ALO", team:"aston", gap:"+31.7", int:"+7.6", tyre:"M", age:24, d:"-0.51", up:false,
     sectors:["hold","hold","green"]},
  {p:9, code:"SAI", team:"williams", gap:"+38.0", int:"+6.3", tyre:"H", age:8, d:"+0.22", up:true,
     sectors:["green","green","hold"]},
  {p:10, code:"HAD", team:"rb", gap:"+44.6", int:"+6.6", tyre:"M", age:19, d:"-0.14", up:false,
     sectors:["hold","green","hold"]},
];

const CIRCUIT = "M175 432C150 382 165 332 220 322C275 312 300 352 340 362C395 376 410 332 380 302C350 274 370 234 420 234C470 234 486 272 531 264C586 254 591 202 561 172C536 147 556 112 611 112C691 112 701 167 661 187C631 202 641 237 691 242C761 250 801 212 841 222C896 234 901 302 851 322C816 336 771 322 741 347C701 380 731 422 681 442C601 472 351 472 261 454C211 444 200 470 175 432Z";
const DRS_ZONES = [[0.90,0.99],[0.30,0.40]];
const CARS = [
  {code:"NOR", team:"mclaren", f:0.00}, {code:"VER", team:"redbull", f:0.033, focus:true},
  {code:"LEC", team:"ferrari", f:0.09}, {code:"PIA", team:"mclaren", f:0.14},
  {code:"RUS", team:"mercedes", f:0.22}, {code:"HAM", team:"ferrari", f:0.31},
];

const STINTS = [
  {code:"NOR", team:"mclaren", stints:[{c:"S",a:0,b:16},{c:"H",a:16,b:53}], pits:[{lap:16,t:"2.3s"}], proj:"—", pk:"flat"},
  {code:"VER", team:"redbull", stints:[{c:"S",a:0,b:14},{c:"H",a:14,b:53}], pits:[{lap:14,t:"2.4s"}], proj:"-2.4", pk:"up", focus:true},
  {code:"LEC", team:"ferrari", stints:[{c:"S",a:0,b:12},{c:"M",a:12,b:53,plan:true}], pits:[{lap:12,t:"2.6s"}], proj:"+1.8", pk:"dn"},
  {code:"PIA", team:"mclaren", stints:[{c:"S",a:0,b:18},{c:"H",a:18,b:53}], pits:[{lap:18,t:"2.5s"}], proj:"+4.0", pk:"dn"},
  {code:"RUS", team:"mercedes", stints:[{c:"M",a:0,b:15},{c:"M",a:15,b:53,plan:true}], pits:[{lap:15,t:"2.7s"}], proj:"+3.1", pk:"dn"},
  {code:"HAM", team:"ferrari", stints:[{c:"S",a:0,b:20},{c:"H",a:20,b:53}], pits:[{lap:20,t:"2.9s"}], proj:"+0.6", pk:"up"},
  {code:"ALO", team:"aston", stints:[{c:"M",a:0,b:8},{c:"H",a:8,b:53}], pits:[{lap:8,t:"3.1s"}], proj:"+9.2", pk:"dn"},
  {code:"SAI", team:"williams", stints:[{c:"S",a:0,b:22},{c:"H",a:22,b:53,plan:true}], pits:[{lap:22,t:"—"}], proj:"+7.4", pk:"dn"},
];
const TOTAL_LAPS = 53, NOW_LAP = 34;
const PIT_WINDOW = [30, 38];

const SCENARIOS = {
  now:  {label:"Şimdi pit", desc:"Bu tur HARD'a in", net:"-2.4", dir:"gain",
         sub:"Undercut LEC üzerine · out-lap trafiği açık",
         cells:[["Pist konumu","P2 → P2","pos"],["Lastik yaşı","0 tur","ok"],["Pit kaybı","21.8s","pos"],["Kalış farkı","+12 tur taze","ok"]],
         traffic:[["HAD",0],["OCO",0],["STR",1]]},
  hold: {label:"4 tur uzat", desc:"HARD'ı 38. tura kadar tut", net:"+1.6", dir:"loss",
         sub:"Temiz hava · LEC overcut riski yüksek",
         cells:[["Pist konumu","P2 → P3","warn"],["Lastik yaşı","13 tur","pos"],["Pit kaybı","21.8s","pos"],["Kalış farkı","LEC 1.1s öne","warn"]],
         traffic:[["LEC",1],["RUS",1]]},
  med:  {label:"Medium'a geç", desc:"Agresif — sona hız", net:"-0.9", dir:"gain",
         sub:"Son 10 turda +0.4s/tur · deg riski",
         cells:[["Pist konumu","P2 → P2","pos"],["Lastik yaşı","0 tur","ok"],["Uçurum riski","Tur 46 civarı","warn"],["Kalış farkı","NOR menzilde","ok"]],
         traffic:[["HAD",0],["STR",0]]},
};

/* ---------- yardımcılar ---------- */
const teamC = (t) => (TEAM[t] || {c:"var(--mute)"}).c;

/* ================= YARIŞ TEKRARI ================= */
function TimingTower(){
  return (
    <div className="pane tower">
      <div className="pane-h"><span className="k">ZAMANLAMA · <b>TUR 34</b></span><span className="aux">ARALIK</span></div>
      <div>
        {GRID.map((r) => (
          <React.Fragment key={r.code}>
            <div className={"tt-row" + (r.focus ? " on" : "")} style={{"--tc": teamC(r.team)}}>
              <div className="tt-pos">{r.p}</div>
              <div className="tt-tk"></div>
              <div className="tt-id">
                <span className="tt-code">{r.code}</span>
                <span className="tt-team">{TEAM[r.team].n}</span>
              </div>
              <div className="tt-right">
                <span className={"tt-gap" + (r.p === 1 ? " leader" : "")}>{r.p === 1 ? "LİDER" : r.int}</span>
                <span className="tt-tyre" style={{"--td": CMP[r.tyre]}}>
                  <span className="tyre-dot">{r.tyre}</span>{r.age}
                </span>
                <span className={"tt-delta " + (r.up ? "up" : "dn")}>{r.d}</span>
              </div>
            </div>
            {r.focus && (
              <div className="tt-detail">
                <span className="slab">SEKTÖR</span>
                {r.sectors.map((s, i) => (
                  <span key={i} className={"sector sg-" + s}><i style={{animationDelay: (i*0.12) + "s"}}></i></span>
                ))}
              </div>
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function CircuitMap({ t }){
  const pathRef = useRef(null);
  const [len, setLen] = useState(0);
  useEffect(() => { if (pathRef.current) setLen(pathRef.current.getTotalLength()); }, []);
  const pos = (f) => {
    if (!pathRef.current || !len) return {x:0,y:0};
    const p = pathRef.current.getPointAtLength(((f % 1) + 1) % 1 * len);
    return { x: p.x, y: p.y };
  };
  const drsPaths = useMemo(() => {
    if (!pathRef.current || !len) return [];
    return DRS_ZONES.map(([a,b]) => {
      const seg=[]; const steps=14;
      for (let i=0;i<=steps;i++){ const p=pathRef.current.getPointAtLength((a+(b-a)*i/steps)*len); seg.push(`${p.x},${p.y}`); }
      return "M"+seg.join(" L");
    });
  }, [len]);

  return (
    <div className="pane stage">
      <div className="pane-h">
        <span className="k">PİST · <b>SUZUKA</b></span>
        <span className="aux">1. VİRAJ TİNTİ · DRS ×2</span>
      </div>
      <div className="map">
        <div className="map-corner">
          <div className="cn">SEKTÖR 2 · 130R</div>
          <div className="cv">VER</div>
          <div className="cs">−0.19s · min 298 km/s</div>
        </div>
        <svg className="circuit" viewBox="0 0 960 512" aria-label="Suzuka pist haritası ve araç konumları">
          <defs>
            <linearGradient id="tg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--cyan)" stopOpacity="0.5"/>
              <stop offset="1" stopColor="var(--cyan)" stopOpacity="0"/>
            </linearGradient>
          </defs>
          <ellipse className="corner-tint" cx="300" cy="320" rx="150" ry="115"/>
          <path className="trk-edge" d={CIRCUIT}/>
          <path className="trk-base" d={CIRCUIT}/>
          <path ref={pathRef} className="trk-line" d={CIRCUIT}/>
          {drsPaths.map((d,i) => <path key={i} className="trk-drs" d={d}/>)}
          <line className="sf-line" x1="432" y1="458" x2="432" y2="486"/>
          {len > 0 && CARS.map((c) => {
            const f = c.f + t * 0.82;
            const {x,y} = pos(f);
            return (
              <g className="car" key={c.code} transform={`translate(${x} ${y})`}>
                {c.focus && <circle className="ring" r="10"><animate attributeName="r" values="9;13;9" dur="2s" repeatCount="indefinite"/></circle>}
                <circle className="body" r={c.focus ? 6.2 : 5.2} fill={teamC(c.team)}/>
                <text textAnchor="middle" dy="3">{c.code[0]}</text>
              </g>
            );
          })}
        </svg>
        <div className="map-legend">
          <span><i style={{background:"var(--cyan)"}}></i><b>Yarış çizgisi</b></span>
          <span><i style={{background:"var(--green)"}}></i><b>DRS bölgesi</b></span>
          <span><i style={{background:"var(--violet)",opacity:.5}}></i><b>1. viraj</b></span>
        </div>
      </div>
      <SpeedTrace t={t}/>
    </div>
  );
}

function SpeedTrace({ t }){
  const W = 640, H = 78;
  const pts = useMemo(() => {
    const arr = [];
    for (let i = 0; i <= 96; i++){
      const x = i / 96;
      const corners = Math.sin(x*Math.PI*6) * 0.5 + Math.sin(x*Math.PI*13 + 1) * 0.28;
      const brake = Math.max(0, Math.sin(x*Math.PI*4 - 0.6)) ** 3;
      const v = 0.55 + corners*0.32 - brake*0.5;
      arr.push([x*W, H - Math.max(0.05, Math.min(1, v)) * H]);
    }
    return arr;
  }, []);
  const shift = Math.round((t * 40) % 96);
  const view = pts.map((p, i) => pts[(i + shift) % pts.length]).map((p, i) => [i / (pts.length-1) * W, p[1]]);
  const dLine = "M" + view.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L");
  const dArea = dLine + ` L${W},${H} L0,${H} Z`;
  const end = view[view.length-1];
  const spd = Math.round(312 - (end[1]/H) * 118);
  return (
    <div className="trace">
      <div className="th">
        <span className="lbl">HIZ İZİ · VER · TUR 34</span>
        <span className="now">{spd}<s>KM/S</s></span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <g className="grid">
          {[0.25,0.5,0.75].map((g,i) => <line key={i} x1="0" y1={H*g} x2={W} y2={H*g}/>)}
        </g>
        <path className="area" d={dArea}/>
        <path className="ln" d={dLine}/>
        <circle className="end" cx={end[0]} cy={end[1]} r="3"/>
      </svg>
    </div>
  );
}

function Meter({ v, kind }){
  return <div className={"meter " + kind}><i style={{width: Math.round(v) + "%"}}></i></div>;
}

function TelemetryRail({ t }){
  const wave = (ph, amp=1) => 0.5 + 0.5*Math.sin(t*Math.PI*2*1.1 + ph);
  const speed = Math.round(206 + wave(0)*118);
  const thr = Math.round(Math.max(0, Math.min(100, (wave(0.4))*120 - 8)));
  const brk = Math.round(Math.max(0, (1 - wave(0.4)) * 90 - 30));
  const gear = Math.max(2, Math.min(8, Math.round(2 + wave(0)*6)));
  const ers = Math.round(40 + wave(1.3)*55);
  const rpm = Math.round(4 + wave(0.1)*8);
  const drsOpen = wave(0.4) > 0.72;
  return (
    <div className="pane rail">
      <div className="pane-h"><span className="k">TELEMETRİ · <b>VER</b></span></div>
      <div className="pane-b">
        <div className="inst">
          <div className="il">{Ico.gauge()} HIZ</div>
          <div className="iv"><span className="n">{speed}</span><span className="u">KM/S</span></div>
        </div>
        <div className="inst">
          <div className="il">{Ico.gauge()} GAZ / FREN</div>
          <Meter v={thr} kind="thr"/>
          <Meter v={brk} kind="brk"/>
        </div>
        <div className="inst">
          <div className="il">{Ico.gear()} VİTES · DEV/DK</div>
          <div className="gearbox">
            <span className="g">{gear}</span>
            <div className="rpm">
              <div className="rpm-bar">
                {Array.from({length:12}).map((_,i) => (
                  <i key={i} className={i < rpm ? (i >= 10 ? "red" : "lit") : ""}></i>
                ))}
              </div>
            </div>
          </div>
        </div>
        <div className="inst">
          <div className="il">{Ico.drs()} DRS</div>
          <div className={"drs " + (drsOpen ? "open" : "shut")}>{drsOpen ? "AÇIK" : "KAPALI"}</div>
        </div>
        <div className="inst">
          <div className="il">{Ico.ers()} ERS AKTARIM</div>
          <Meter v={ers} kind="ers"/>
          <div className="iv" style={{marginTop:6}}><span className="n" style={{fontSize:16}}>{Math.round(ers*3.4)}</span><span className="u">KJ / TUR</span></div>
        </div>
        <div className="inst">
          <div className="il">{Ico.tyre()} LASTİK · HARD</div>
          <div className="iv" style={{alignItems:"center"}}>
            <span className="n" style={{fontSize:18}}>9</span><span className="u">TUR</span>
            <span style={{marginLeft:"auto",fontFamily:"var(--f-data)",fontWeight:600,fontSize:11,color:"var(--dim)"}}>92°/88°C</span>
          </div>
        </div>
        <div className="inst">
          <div className="il">{Ico.gauge()} SON TUR · Δ</div>
          <div className="iv" style={{alignItems:"baseline"}}>
            <span className="n" style={{fontSize:19}}>1:31.447</span>
            <span className="u" style={{marginLeft:"auto",color:"var(--green)"}}>−0.19</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Transport({ playing, setPlaying, lap, speed, setSpeed }){
  const pct = (lap / TOTAL_LAPS) * 100;
  const pits = [
    {lap:14, pc:"var(--pink)"}, {lap:16, pc:"#dfe6ee"}, {lap:20, pc:"#dfe6ee"}, {lap:12, pc:"var(--amber)"},
  ];
  return (
    <div className="pane scrub">
      <div className="pane-b">
        <div className="transport">
          <button className="play" onClick={() => setPlaying(p => !p)} aria-label={playing ? "Duraklat" : "Oynat"}>
            {playing ? Ico.pause() : Ico.play()}
          </button>
          <span className="lapread">TUR {String(lap).padStart(2,"0")} <s>/ {TOTAL_LAPS}</s></span>
          <div className="speedsel" role="group" aria-label="Oynatma hızı">
            {[0.5,1,2].map(s => (
              <button key={s} aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}×</button>
            ))}
          </div>
          <div className="timeline">
            <div className="track"></div>
            <div className="fill" style={{width: pct + "%"}}></div>
            <div className="sc" style={{left: (18/TOTAL_LAPS*100)+"%", width: (5/TOTAL_LAPS*100)+"%"}}></div>
            {Array.from({length:TOTAL_LAPS+1}).map((_,i) => (
              <div key={i} className={"tick" + (i % 10 === 0 ? " v" : "")} style={{left: (i/TOTAL_LAPS*100)+"%"}}></div>
            ))}
            {[0,10,20,30,40,50].map(i => (
              <span key={i} className="cap" style={{left: (i/TOTAL_LAPS*100)+"%"}}>{i}</span>
            ))}
            {pits.map((p,i) => (
              <div key={i} className="pit" style={{left: (p.lap/TOTAL_LAPS*100)+"%", "--pc": p.pc}}></div>
            ))}
            <div className="head" style={{left: pct + "%"}}></div>
          </div>
        </div>
        <div className="eventlog">
          <span className="ev"><span className="lap">T34</span><b>SC</b> devrede — 130R'de kırılma</span>
          <span className="ev"><span className="lap">T33</span>VER <b>DRS</b> menzilinde · 0.9s</span>
          <span className="ev"><span className="lap">T20</span>HAM pit <b>2.9s</b> → HARD</span>
          <span className="ev"><span className="lap">T16</span>NOR pit <b>2.3s</b> → HARD · lider kaldı</span>
          <span className="ev"><span className="lap">T14</span>VER pit <b>2.4s</b> → HARD · undercut LEC</span>
        </div>
      </div>
    </div>
  );
}

function RaceReplay(){
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [t, setT] = useState(0);
  const [lap, setLap] = useState(NOW_LAP);
  const raf = useRef(0), last = useRef(0);
  useEffect(() => {
    if (!playing) return;
    const tick = (now) => {
      if (!last.current) last.current = now;
      const dt = Math.min(0.05, (now - last.current) / 1000);
      last.current = now;
      setT(v => v + dt * 0.05 * speed);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf.current); last.current = 0; };
  }, [playing, speed]);
  useEffect(() => {
    const id = setInterval(() => { if (playing) setLap(l => l >= TOTAL_LAPS ? NOW_LAP : l + 1); }, 4200 / speed);
    return () => clearInterval(id);
  }, [playing, speed]);

  return (
    <div className="replay">
      <TimingTower/>
      <CircuitMap t={t}/>
      <TelemetryRail t={t}/>
      <Transport playing={playing} setPlaying={setPlaying} lap={lap} speed={speed} setSpeed={setSpeed}/>
    </div>
  );
}

/* ================= STRATEJİ DUVARI ================= */
function StrategyChips({ pick, setPick }){
  return (
    <div className="pane">
      <div className="pane-h"><span className="k">STRATEJİ · <b>PLAN</b></span><span className="aux">Δ LİDER</span></div>
      <div>
        {STINTS.map(s => (
          <div key={s.code} className={"chip" + (s.focus ? " on" : "")} style={{"--tc": teamC(s.team)}}
               onClick={() => setPick(s.code)}>
            <div className="top">
              <span className="cpos">P{STINTS.indexOf(s)+1}</span>
              <span className="ctk"></span>
              <span className="ccode">{s.code}</span>
              <span className={"cproj " + s.pk}>{s.proj}</span>
            </div>
            <div className="seq">
              {s.stints.map((st, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <span className="arw">→</span>}
                  <span className={"cmp" + (st.plan ? " plan" : "")} style={{"--cc": CMP[st.c]}}>{st.c}</span>
                </React.Fragment>
              ))}
            </div>
            <div className="meta">
              <span><b>{s.pits.length}</b> durak</span>
              <span>son <b>{s.pits[s.pits.length-1].t}</b></span>
              <span>bitiş <b>T{s.stints[s.stints.length-1].b}</b></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Gantt(){
  const pct = (lap) => (lap / TOTAL_LAPS) * 100;
  return (
    <div className="pane">
      <div className="pane-h">
        <span className="k">STINT ZAMAN ÇİZELGESİ · <b>53 TUR</b></span>
        <span className="aux">◧ pit penceresi · ┃ şu an</span>
      </div>
      <div className="gantt">
        {STINTS.map((s, li) => (
          <div className="glane" key={s.code}>
            <div className="glab" style={{"--tc": teamC(s.team)}}><i></i>{s.code}</div>
            <div className="gtrack">
              {li === 0 && (
                <div className="pitwin" style={{left: pct(PIT_WINDOW[0]) + "%", width: (pct(PIT_WINDOW[1]) - pct(PIT_WINDOW[0])) + "%", top:0, height: 34*STINTS.length}}></div>
              )}
              {s.stints.map((st, i) => (
                <div key={i} className={"stint" + (st.plan ? " plan" : "")}
                     style={{left: pct(st.a) + "%", width: (pct(st.b) - pct(st.a)) + "%", "--cc": CMP[st.c]}}>
                  {st.c}
                </div>
              ))}
              {s.pits.map((p, i) => (
                <div key={i} className="pitmark" data-t={p.t} style={{left: pct(p.lap) + "%"}}></div>
              ))}
              {li === 0 && <div className="nowline" style={{left: pct(NOW_LAP) + "%", height: 34*STINTS.length}}></div>}
            </div>
          </div>
        ))}
        <div className="glane" style={{border:0}}>
          <div className="glab"></div>
          <div className="axis">
            {[0,10,20,30,40,50].map(l => (
              <span key={l} className="x" style={{left: pct(l) + "%"}}>{l}</span>
            ))}
          </div>
        </div>
        <div style={{gridColumn:2, paddingLeft:10, marginTop:6, fontFamily:"var(--f-data)", fontSize:9.5, letterSpacing:".06em", color:"var(--green)"}}>
          ↑ VER T14 undercut → LEC · +2.4s net kazanç
        </div>
      </div>
    </div>
  );
}

function WhatIf({ pick }){
  const [scn, setScn] = useState("now");
  const s = SCENARIOS[scn];
  return (
    <div className="pane wi">
      <div className="pane-h"><span className="k">SENARYO · <b>{pick}</b></span><span className="aux">HARD · 13 TUR</span></div>
      <div className="scn" role="group" aria-label="Senaryo seçimi">
        {Object.entries(SCENARIOS).map(([k, v]) => (
          <button key={k} aria-pressed={scn === k} onClick={() => setScn(k)}>
            <span className="dot"></span>
            <span>
              <span className="sn">{v.label}</span>
              <span className="sd">{v.desc}</span>
            </span>
          </button>
        ))}
      </div>
      <div className="wi-out">
        <div className={"wi-net " + s.dir}>
          <span className="n">{s.net}</span><span className="u">s net / lider</span>
        </div>
        <div className="wi-sub">{s.sub}</div>
        <div className="wi-grid">
          {s.cells.map((c, i) => (
            <div className="wi-cell" key={i}>
              <div className="l">{c[0]}</div>
              <div className={"v " + c[2]}>{c[1]}</div>
            </div>
          ))}
        </div>
        <div className="traffic">
          <div className="l">Out-lap trafiği</div>
          <div className="cars">
            {s.traffic.map((c, i) => (
              <span key={i} className={"cc2" + (c[1] ? " hot" : "")}>{c[0]}</span>
            ))}
          </div>
        </div>
      </div>
      <div className="tyrelife">
        <div className="th"><span className="lbl">LASTİK ÖMRÜ · HARD</span><span className="age">13<span style={{color:"var(--mute)",fontWeight:500}}> tur</span></span></div>
        <div className="lifebar">
          <div className="used" style={{width: "38%"}}></div>
          <div className="cliff" style={{left: "72%"}}></div>
        </div>
        <div className="rows">
          <div><div className="l">Tahmini deg</div><div className="v" style={{color:"var(--green)"}}>0.09 s/tur</div></div>
          <div><div className="l">Kalan pencere</div><div className="v">≈ 22 tur</div></div>
          <div><div className="l">Uçurum</div><div className="v" style={{color:"var(--amber)"}}>T48±3</div></div>
        </div>
      </div>
    </div>
  );
}

function StrategyWall(){
  const [pick, setPick] = useState("VER");
  return (
    <div className="sw">
      <StrategyChips pick={pick} setPick={setPick}/>
      <Gantt/>
      <WhatIf pick={pick}/>
    </div>
  );
}

/* ================= KABUK ================= */
function App(){
  const [view, setView] = useState("replay");
  return (
    <div>
      <div className="views" role="group" aria-label="Görünüm">
        <button aria-pressed={view === "replay"} onClick={() => setView("replay")}>{Ico.replay()} Yarış Tekrarı</button>
        <button aria-pressed={view === "wall"} onClick={() => setView("wall")}>{Ico.wall()} Strateji Duvarı</button>
      </div>
      {view === "replay" ? <RaceReplay/> : <StrategyWall/>}
    </div>
  );
}

/* ================= DIŞA AKTAR ================= */
export { RaceReplay as RaceReplayHUD, StrategyWall as StrategyWallHUD, App as FormulaPaddockHUD, Ico as MotorsportIcons };
