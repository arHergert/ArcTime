
let allMovies=[], allSeries=[], allGames=[], currentTab='movies', tabQueries={};
let diary=[], diaryFilter='all', modalItem=null, modalSeason=null, awards={};
let recommendations=[], recFilter='all', recLoaded=false;
let diaryYearsShown=1, diaryLoadMoreObs=null;
// Index des offenen Modal-Items in der aktuellen Grid-Liste (für ←/→-Navigation
// im Modal) - null, wenn das Modal nicht über das Grid geöffnet wurde
// (z.B. Tagebuch-Stub-Eintrag ohne Bibliotheks-Match), dann ist Blättern deaktiviert.
let modalGridIdx=null;

// ── Award-Kategorien ──────────────────────────────────────────────────────────
// elig: welche Einträge nominierbar sind
//   all | new | continued | completed | mini | notContinued
const CATS_FILM=[
  {id:'f_best',       n:'Bester Film'},
  {id:'f_story',      n:'Beste Geschichte'},
  {id:'f_storytell',  n:'Beste Erzählweise'},
  {id:'f_screenplay', n:'Bestes Drehbuch'},
  {id:'f_director',   n:'Beste Regie'},
  {id:'f_actor',      n:'Bester Schauspieler',   person:true},
  {id:'f_actress',    n:'Beste Schauspielerin',  person:true},
  {id:'f_comedy',     n:'Beste Komödie'},
  {id:'f_magic',      n:'Bester magischer Moment'},
  {id:'f_surprise',   n:'Beste Überraschung'},
  {id:'f_emotional',  n:'Emotionalster Film'},
  {id:'f_atmosphere', n:'Beste Atmosphäre'},
  {id:'f_picture',    n:'Bestes Bild'},
  {id:'f_music',      n:'Beste Musik'},
  {id:'f_worst',      n:'Schlechtester Film'},
];
const CATS_SERIES=[
  {id:'s_best',      n:'Beste Serie',                   elig:'notContinued',
   hint:'Neu, Mini oder abgeschlossen'},
  {id:'s_newcomer',  n:'Beste neue Serie',              elig:'new',
   hint:'Dieses Jahr erstmalig gesehen'},
  {id:'s_completed', n:'Beste abgeschlossene Serie',    elig:'completed'},
  {id:'s_mini',      n:'Beste Mini-Serie',              elig:'mini'},
  {id:'s_cont_drama',n:'Beste fortgesetzte Staffel – Drama',   elig:'continued'},
  {id:'s_cont_com',  n:'Beste fortgesetzte Staffel – Komödie', elig:'continued'},
  {id:'s_comedy',    n:'Beste Komödie'},
  {id:'s_story',     n:'Beste Geschichte'},
  {id:'s_writing',   n:'Beste Dialoge'},
  {id:'s_actor',     n:'Bester Schauspieler',   person:true},
  {id:'s_actress',   n:'Beste Schauspielerin',  person:true},
  {id:'s_letdown',   n:'Enttäuschung des Jahres'},
];
const CATS_GAMES=[
  {id:'g_soundtrack',    n:'Bester Soundtrack',      text:true, hint:'Freitext (OST/Titel)'},
  {id:'g_music',         n:'Beste Musik'},
  {id:'g_worldbuilding', n:'Bestes Welt- und Leveldesign'},
  {id:'g_indie',         n:'Bestes Indie-Spiel'},
  {id:'g_magic',         n:'Bester magischer Moment'},
  {id:'g_comedy',        n:'Bestes komisches oder surreales Spiel'},
  {id:'g_letdown',       n:'Enttäuschung des Jahres'},
  {id:'g_atmosphere',    n:'Beste Atmosphäre'},
  {id:'g_character',     n:'Bester Charakter',        text:true, hint:'Freitext (Charaktername)'},
  {id:'g_oldgame',       n:'Bestes altes Spiel'},
  {id:'g_action',        n:'Beste Action'},
  {id:'g_narrative',     n:'Beste Erzählgestaltung'},
  {id:'g_story',         n:'Beste Geschichte'},
  {id:'g_best',          n:'Bestes Spiel'},
];
const CAT_BY_ID={};
[...CATS_FILM,...CATS_SERIES,...CATS_GAMES].forEach(c=>CAT_BY_ID[c.id]=c);

function catKind(cat){return cat.id[0]==='f'?'movie':cat.id[0]==='s'?'season':'game';}

// ── Serien-Lifecycle ──────────────────────────────────────────────────────────
// tmdbSeasonTotals: {tmdbId: {total, ended, latestSeasonNumber}}, per TMDB nachgeladen
let tmdbSeasonTotals={};

// Ermittelt für einen Staffel-Eintrag: started / continued / completed
function seriesStatus(e){
  if(e.kind!=='season') return null;
  if(e.statusOverride) return e.statusOverride;

  // Alle Einträge derselben Serie (Key, sonst Titel)
  const same=diary.filter(x=>x.kind==='season'&&
    (e.key? x.key===e.key : normTitle(x.title)===normTitle(e.title)));

  // Gesamtstaffelzahl: zuerst aus der Bibliothek, sonst per TMDB nachgeladen
  let total=null, endedOk=true;
  if(e.key){
    const lib=allSeries.find(s=>s.key===e.key);
    if(lib&&lib.seasons) total=parseInt(lib.seasons)||null;
  }
  if(!total&&e.tmdb_id&&tmdbSeasonTotals[e.tmdb_id]){
    const info=tmdbSeasonTotals[e.tmdb_id];
    total=info.total||null;
    // TMDBs "number_of_seasons" ist nur die bisher erschienene Staffelzahl, keine
    // Garantie dass keine weitere kommt. "Completed" nur, wenn die Serie laut TMDB
    // auch wirklich beendet/abgesetzt ist - sonst z.B. bei laufenden Serien fälschlich
    // "Completed" nur weil man die neueste (aber nicht letzte) Staffel gesehen hat.
    endedOk=!!info.ended;
  }
  if(e.mini){ total=total||1; endedOk=true; }

  // Abgeschlossen: letzte Staffel gesehen UND Serie ist wirklich zu Ende
  if(total&&endedOk&&(e.season||0)>=total) return 'completed';

  // "Started" ist nur für Staffel 1 möglich (bzw. Mini-Serien) - bei Staffel >1
  // muss zwingend vorher etwas geschaut worden sein, auch wenn das nicht geloggt ist.
  if((e.season||1)>1) return 'continued';

  // Erstmalig: frühester Eintrag dieser Serie
  const sorted=[...same].sort((a,b)=>
    (a.date||'').localeCompare(b.date||'')||(a.created||0)-(b.created||0));
  if(sorted.length&&sorted[0].id===e.id) return 'started';

  return 'continued';
}

// Manuelles Setzen/Zurücksetzen des Status-Badges direkt aus dem Tagebuch
// (ohne das große Bewertungs-Modal öffnen zu müssen).
async function toggleRewatch(id){
  const e=diary.find(x=>x.id===id);
  if(!e) return;
  e.rewatch=!e.rewatch;
  try{
    await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id, rewatch:e.rewatch})
    });
  }catch{ showToast('⚠ Rewatch konnte nicht gespeichert werden',true); }
  renderDiary();
}

async function setStatusOverride(selectEl){
  const id=selectEl.dataset.id;
  const value=selectEl.value||null;
  const e=diary.find(x=>x.id===id);
  if(!e) return;
  e.statusOverride=value;
  try{
    await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id, statusOverride:value})
    });
  }catch{ showToast('⚠ Status konnte nicht gespeichert werden',true); }
  renderDiary();
}

// Lädt fehlende Gesamtstaffelzahlen per TMDB nach (für Einträge ohne Bibliotheks-Match,
// z.B. aus dem Serializd-Import) - einmalig pro eindeutiger tmdb_id, gecacht am Server.
async function loadTmdbSeasonTotals(){
  const ids=[...new Set(diary
    .filter(e=>e.kind==='season'&&e.tmdb_id&&!tmdbSeasonTotals[e.tmdb_id])
    .map(e=>e.tmdb_id))];
  if(!ids.length) return;
  try{
    const d=await fetch('/api/tmdb/seasons?ids='+ids.join(',')).then(r=>r.json());
    if(d.ok&&d.shows) tmdbSeasonTotals={...tmdbSeasonTotals, ...d.shows};
  }catch{ /* stumm ignorieren - Status bleibt best-effort ohne TMDB-Daten */ }
}

function normTitle(s){return (s||'').toLowerCase().replace(/[^a-z0-9]/g,'');}

// Ist der Eintrag für eine Kategorie zulässig?
function eligible(e,cat){
  const c=CAT_BY_ID[cat]; if(!c) return false;
  if(catKind(c)!==e.kind) return false;
  if(!c.elig) return true;
  const st=seriesStatus(e);
  switch(c.elig){
    case 'new':          return st==='started';
    case 'continued':    return st==='continued';
    case 'completed':    return st==='completed';
    case 'mini':         return !!e.mini;
    case 'notContinued': return st!=='continued'||!!e.mini;
    default:             return true;
  }
}

const STATUS_LBL={started:'Begonnen',continued:'Fortgesetzt',completed:'Abgeschlossen'};

// ── Game-Status (spielt gerade / abgebrochen / abgeschlossen) ─────────────────
const GAME_STATUS_LBL={playing:'Spielt gerade',dropped:'Abgebrochen',completed:'Abgeschlossen'};
function gameStatus(e){
  if(e.kind!=='game') return null;
  if(e.statusOverride) return e.statusOverride;
  return e.finishDate?'completed':'playing';
}
// Start- und Ende-Datum eines Spiels werden im Tagebuch nur dann zu einem
// Eintrag kombiniert, wenn dazwischen nichts anderes geloggt wurde. Liegt ein
// anderer Film-/Serien-/Spiel-Eintrag zeitlich zwischen Start und Ende, wird
// stattdessen ein "Gestartet"- und ein "Beendet/Abgebrochen"-Eintrag gezeigt.
function gameHasEntryBetween(e){
  if(!e.startDate||!e.finishDate||e.startDate>=e.finishDate) return false;
  return diary.some(o=>o.id!==e.id&&o.date&&o.date>e.startDate&&o.date<e.finishDate);
}

// Platzhalter-Karten, solange die erste Antwort von /api/data noch nicht da
// ist - damit die Seite sofort etwas zeigt statt eine leere Fläche.
function skeletonCard(){
  return `<div class="skel-card">
    <div class="skel-poster"></div>
    <div class="skel-cb">
      <div class="skel-line w60"></div>
      <div class="skel-line w40"></div>
    </div>
  </div>`;
}
function renderSkeleton(){
  const html=Array.from({length:14}).map(skeletonCard).join('');
  const gm=document.getElementById('grid-movies'), gs=document.getElementById('grid-series');
  if(gm&&!gm.innerHTML) gm.innerHTML=html;
  if(gs&&!gs.innerHTML) gs.innerHTML=html;
}

async function init(){
  renderSkeleton();
  const savedTab=getSavedTab();
  const d=await fetch('/api/data').then(r=>r.json());
  allMovies=d.movies||[]; allSeries=d.series||[]; allGames=d.games||[];
  // Bibliothek sofort anzeigen, unabhängig vom (potenziell langsameren) Tagebuch-Laden.
  // Wenn zuletzt Filme/Serien offen war, direkt den richtigen Tab zeigen (kein Zwischen-
  // aufblitzen von "Filme"); bei Tagebuch/Rückblick/Empfehlungen muss erst das Tagebuch
  // geladen sein, das passiert weiter unten.
  if(savedTab==='series') switchTab('series');
  else { buildGenreFilter(); render(); }
  if(d.omdb_warning) showToast('⚠ '+d.omdb_warning, true);
  await loadDiary();
  if(savedTab&&savedTab!==currentTab) switchTab(savedTab);
  else {
    // Die Sortierung "Zuletzt geschaut" hängt vom Tagebuch ab, das gerade erst
    // geladen wurde -> aktuell sichtbare Ansicht neu rendern, sonst zeigt das
    // Grid eine andere Reihenfolge als getList() beim nächsten Klick liefert.
    refreshVisibleView();
  }
}

// Rendert die aktuell aktive Ansicht neu (nach Änderungen an diary/allMovies/...).
function refreshVisibleView(){
  if(currentTab==='diary') renderDiary();
  else if(currentTab==='stats') renderStats();
  else if(currentTab==='recommendations') renderRecommendations();
  else render();
}

async function loadDiary(){
  try{
    const d=await fetch('/api/diary').then(r=>r.json());
    diary=d.entries||[];
  }catch{ diary=[]; }
  try{
    const a=await fetch('/api/awards').then(r=>r.json());
    awards=a.awards||{};
  }catch{ awards={}; }
  try{
    const t=await fetch('/api/top').then(r=>r.json());
    topLists=t.top||{};
  }catch{ topLists={}; }
  // Im Hintergrund nachladen - blockiert die erste Anzeige nicht, aktualisiert
  // dann bei Bedarf die Status-Badges (v.a. "Completed"-Erkennung ohne Bibliotheks-Match).
  loadTmdbSeasonTotals().then(()=>{ if(currentTab==='diary') renderDiary(); });
}

// ── Reload ────────────────────────────────────────────────────────────────────
function doReload(){
  const btn=document.getElementById('reloadBtn');
  const icon=document.getElementById('reloadIcon');
  btn.classList.add('loading');
  icon.className='spin'; icon.textContent='↻';
  showProg(0,0,'Scanne Ordner…');
  const es=new EventSource('/api/reload');
  es.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d.done){
      es.close();
      allMovies=d.data.movies||[]; allSeries=d.data.series||[];
      buildGenreFilter(); render(); hideProg();
      btn.classList.remove('loading');
      icon.className=''; icon.textContent='↻';
      if(d.warning) showToast('⚠ '+d.warning, true);
    } else { showProg(d.n,d.total,d.msg); }
  };
  es.onerror=()=>{es.close();hideProg();btn.classList.remove('loading');icon.className='';icon.textContent='↻';};
}
function showProg(n,t,msg){
  document.getElementById('prog-wrap').style.display='flex';
  document.getElementById('prog-msg').textContent=t>0?`${msg} (${n}/${t})`:msg;
  document.getElementById('prog-bar').style.width=t>0?(n/t*100)+'%':'0%';
}
function hideProg(){document.getElementById('prog-wrap').style.display='none';}

// ── Tabs ──────────────────────────────────────────────────────────────────────
const TABS=['movies','series','diary','stats','recommendations','releases'];
const GENRE_DE={
  Action:'Action',Adventure:'Abenteuer',Animation:'Animation',Biography:'Biografie',
  Comedy:'Komödie',Crime:'Krimi',Documentary:'Dokumentation',Drama:'Drama',
  Fantasy:'Fantasy',History:'Historie',Horror:'Horror',Music:'Musik',
  Musical:'Musical',Mystery:'Mystery',Romance:'Romantik',
  'Sci-Fi':'Science-Fiction','Science Fiction':'Science-Fiction',Sport:'Sport',
  'TV Movie':'Fernsehfilm',Thriller:'Thriller',War:'Krieg',Western:'Western',
  'Role-playing (RPG)':'Rollenspiel',Puzzle:'Rätsel',Indie:'Indie',
  Family:'Familie',Politics:'Politik',Shooter:'Shooter',Platform:'Plattformer',Strategy:'Strategie',
  Simulator:'Simulation',Racing:'Rennspiel',"Hack and slash/Beat 'em up":"Hack'n'Slay",
  'Real Time Strategy (RTS)':'Echtzeitstrategie','Turn-based strategy (TBS)':'Rundenstrategie',
  Tactical:'Taktik','Point-and-click':'Point & Click',Fighting:'Kampfspiel','Visual Novel':'Visual Novel'
};
function genreLabel(genre){ return GENRE_DE[genre]||genre; }
const LAST_TAB_KEY='arctime_lastTab';
function getSavedTab(){
  try{
    const t=localStorage.getItem(LAST_TAB_KEY);
    return (t&&TABS.includes(t))?t:null;
  }catch{ return null; }
}

function switchTab(tab){
  if(tab!==currentTab) tabQueries[currentTab]=document.getElementById('search').value;
  currentTab=tab;
  try{ localStorage.setItem(LAST_TAB_KEY,tab); }catch{}
  const idx=TABS.indexOf(tab);
  document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('active',i===idx));
  TABS.forEach(t=>document.getElementById('section-'+t).classList.toggle('active',t===tab));

  const isLib=(tab==='movies'||tab==='series');
  const hasSearch=isLib||tab==='diary'||tab==='recommendations';
  const search=document.getElementById('search');
  // visibility statt display: die Kopfzeile behält so auf jedem Tab dieselbe
  // Höhe/Breitenaufteilung - kein Springen/Reflow mehr beim Tab-Wechsel (z.B.
  // Rückblick, wo weder Suche noch Controls/Zähler/Buttons gebraucht werden).
  search.style.visibility=hasSearch?'visible':'hidden';
  search.value=tabQueries[tab]||'';
  search.placeholder=tab==='diary'?'Tagebuch durchsuchen…':
    tab==='recommendations'?'Empfehlungen durchsuchen…':
    tab==='series'?'Serien durchsuchen…':'Filme durchsuchen…';
  document.querySelector('.controls').style.visibility=isLib?'visible':'hidden';
  document.getElementById('countLabel').style.visibility=isLib?'visible':'hidden';
  document.getElementById('reloadBtn').style.visibility=isLib?'visible':'hidden';
  document.getElementById('randomBtn').style.visibility=isLib?'visible':'hidden';
  document.getElementById('empty').style.display='none';

  if(tab==='diary'){ renderDiary(); }
  else if(tab==='stats'){ buildYearSelect(); renderStats(); }
  else if(tab==='recommendations'){ loadRecommendationsIfNeeded(); }
  else if(tab==='releases'){ renderReleases(); loadReleases(true); loadRelSuggestions(); }
  else { buildGenreFilter(); render(); }
}

// Die Kopfzeilen-Suche filtert nur Ansichten mit Titel-Listen.
function onSearchInput(){
  tabQueries[currentTab]=document.getElementById('search').value;
  diaryYearsShown=1;
  if(currentTab==='diary') renderDiary();
  else if(currentTab==='recommendations') renderRecommendations();
  else if(currentTab==='movies'||currentTab==='series') render();
}

// ── Liste ─────────────────────────────────────────────────────────────────────
function getList(){
  const q=document.getElementById('search').value.toLowerCase();
  const sort=document.getElementById('sortBy').value;
  const genre=document.getElementById('filterGenre').value;
  const src=currentTab==='movies'?allMovies:allSeries;
  let list=src.filter(m=>{
    if(q&&!(m.t||'').replace(/\./g,' ').toLowerCase().includes(q.replace(/\./g,' ')))return false;
    if(genre){
      const aliases=genre==='Sci-Fi'||genre==='Science Fiction'
        ?['Sci-Fi','Science Fiction']:[genre];
      if(!aliases.some(g=>(m.genre||'').includes(g)))return false;
    }
    return true;
  });
  if(sort==='watched'){
    const lw=lastWatchedMap();
    list.sort((a,b)=>{
      const da=lw[a.key]||'', db=lw[b.key]||'';
      if(da&&db) return db.localeCompare(da);
      if(da&&!db) return -1;
      if(!da&&db) return 1;
      return (a.t||'').localeCompare(b.t||'');
    });
  } else {
    list.sort((a,b)=>{
      if(sort==='title')  return(a.t||'').localeCompare(b.t||'');
      if(sort==='year')   return+(b.y||0)-+(a.y||0);
      if(sort==='rating') return(b.rating||0)-(a.rating||0);
      if(sort==='runtime')return toMin(b.runtime)-toMin(a.runtime);
      if(sort==='added')  return(b.added||0)-(a.added||0);
      return 0;
    });
  }
  return list;
}
function toMin(s){return parseInt((s||'').replace(/\D/g,''))||0;}

// Neuestes Sehdatum je Bibliotheks-Key aus dem Tagebuch (für "Zuletzt geschaut")
function lastWatchedMap(){
  const map={};
  // Zuletzt abgespielt (auch ohne Tagebuch-Eintrag) als Basis nehmen…
  (currentTab==='movies'?allMovies:allSeries).forEach(m=>{
    if(m.key&&m.last_played) map[m.key]=m.last_played;
  });
  // …und mit Tagebuch-Daten überschreiben, falls neuer
  diary.forEach(e=>{
    if(!e.key||!e.date) return;
    if(!map[e.key]||e.date>map[e.key]) map[e.key]=e.date;
  });
  return map;
}

// ── Render ────────────────────────────────────────────────────────────────────
function render(){
  if(currentTab!=='movies'&&currentTab!=='series')return;
  const list=getList();
  const gid=currentTab==='movies'?'grid-movies':'grid-series';
  const lbl=currentTab==='movies'?' Filme':' Serien';
  document.getElementById('countLabel').textContent=list.length+lbl;

  const emptyEl=document.getElementById('empty');
  emptyEl.style.display=list.length?'none':'block';
  if(!list.length){
    const src=currentTab==='movies'?allMovies:allSeries;
    const q=document.getElementById('search').value.trim();
    const genre=document.getElementById('filterGenre').value;
    const ordner=currentTab==='movies'?'Filme':'Serien';
    if(!src.length){
      // Bibliothek insgesamt leer - vermutlich erster Start, kein Filter-Problem
      emptyEl.innerHTML=`Noch keine ${lbl.trim()} gefunden.<br>
        <span style="font-size:12.5px">Lege sie im Ordner <code>${ordner}</code> neben mediathek.py ab
        und klicke oben rechts auf ${'↻'} zum Aktualisieren.</span>`;
    } else if(q||genre){
      emptyEl.innerHTML=`Keine Treffer${q?` für „${esc(q)}“`:''}${genre?` bei Genre „${esc(genreLabel(genre))}“`:''}.<br>
        <span style="font-size:12.5px">Suche oder Genre-Filter anpassen.</span>`;
    } else {
      emptyEl.innerHTML='Keine Einträge gefunden.';
    }
  }
  document.getElementById(gid).innerHTML=list.map((m,i)=>card(m,i)).join('');
}

function card(m,idx){
  const rc=rCls(m.rating);
  const poster=m.poster
    ?`<img src="${m.poster}" alt="${esc(m.t)}" loading="lazy">`
    :`<div class="pp"><span>${esc(m.t)}</span></div>`;
  const srcLbl=m.rating_source==='tmdb'?'TMDB':'IMDb';
  const badge=m.rating?`<div class="rb ${rc}"><span class="rb-src">${srcLbl}</span> ${m.rating.toFixed(1)}</div>`:'';

  // Qualitätsbadges
  let qhtml='';
  {
    const rb=m.res==='4K'?'<span class="qb qb-4k">4K</span>'
             :m.res==='FHD'?'<span class="qb qb-fhd">FHD</span>'
             :m.res==='HD'?'<span class="qb qb-hd">HD</span>':'';
    // Dolby-Vision-Label bewusst nicht angezeigt (nur HDR) - der Datei bleibt
    // ihr hdr==='DV'-Wert intern trotzdem erhalten, u.a. fürs Erkennen von
    // HDR-Inhalten für die "Auf LG TV abspielen"-Funktion.
    const hb=m.hdr==='HDR'?'<span class="qb qb-hdr">HDR</span>':'';
    // Explizit false = per ffprobe geprüft und wirklich keine eingebetteten
    // Untertitel gefunden (nicht einfach ungeprüft/unbekannt, sonst würde
    // ohne ffmpeg jeder Film das Icon bekommen).
    const nsb=m.has_subs===false
      ?`<span class="qb qb-nosubs" title="Keine eingebetteten Untertitel gefunden">
          <span class="fa-stack"><i class="fa-solid fa-closed-captioning fa-stack-1x"></i>
          <i class="fa-solid fa-slash fa-stack-1x"></i></span>
        </span>`:'';
    if(rb||hb||nsb) qhtml=`<div class="qbs">${rb}${hb}${nsb}</div>`;
  }

  // Play-Button: data-idx Attribut, onclick ohne Pfad-Embedding
  const playBtn=m.file
    ?`<div class="card-play" data-idx="${idx}" onclick="playCard(event,this)" title="Abspielen">▶</div>`
    :'';

  // Nur 1 Genre-Chip pro Karte statt 2 - weniger visuelles Rauschen im Grid,
  // die Genre-Filterung oben deckt den Rest bereits ab.
  const genres=(m.genre||'').split(',').map(g=>g.trim()).filter(Boolean).slice(0,1);
  const meta=[m.y,m.runtime].filter(Boolean).join(' · ');

  // Eigene Bewertung (Film: direkt, Serie: Durchschnitt aller Staffeln)
  const mine=diary.filter(e=>e.key===m.key&&e.rating);
  let myStars='';
  if(mine.length){
    const avg=mine.reduce((s,e)=>s+e.rating,0)/mine.length;
    myStars=`<div class="cg">${starsHTML(Math.round(avg*2)/2,true)}</div>`;
  }

  // Schnell-Loggen: nur bei Filmen (bei Serien braucht es erst die
  // Staffelauswahl, die im Modal passiert - dort steht die Bewertung jetzt ganz oben)
  const rateBtn=currentTab==='movies'
    ?`<div class="card-rate${mine.length?' logged':''}" data-idx="${idx}"
        onclick="openQuickRate(event,this)" title="Schnell bewerten">★</div>`
    :'';

  const fixBtn=`<div class="card-fix" data-idx="${idx}"
      onclick="openTitleFix(event,this)" title="Falsch erkannt? Titel korrigieren">
      <i class="fa-solid fa-pen"></i></div>`;

  return `<div class="card" onclick="openModal(${idx})">
    <div class="pw">${poster}${badge}${playBtn}${rateBtn}${fixBtn}</div>
    <div class="cb">
      <div class="ct">${esc(m.t)}</div>
      ${(meta||qhtml)?`<div class="card-info">${meta?`<div class="cm">${esc(meta)}</div>`:''}${qhtml}</div>`:''}
      ${myStars}
      ${genres.length?`<div class="cg">${genres.map(g=>`<span class="gt">${esc(genreLabel(g))}</span>`).join('')}</div>`:''}
    </div>
  </div>`;
}

function rCls(r){return r>=8?'high':r>=6.5?'mid':'low';}

function buildGenreFilter(){
  const genres=new Map();
  (currentTab==='movies'?allMovies:allSeries)
    .forEach(m=>(m.genre||'').split(',').forEach(g=>{
      const t=g.trim(),label=genreLabel(t);
      if(t&&!genres.has(label)) genres.set(label,t);
    }));
  const sel=document.getElementById('filterGenre'),prev=sel.value;
  sel.innerHTML='<option value="">Alle Genres</option>';
  [...genres.entries()].sort((a,b)=>a[0].localeCompare(b[0],'de')).forEach(([label,g])=>{
    const o=document.createElement('option');
    o.value=g;o.textContent=label;if(g===prev)o.selected=true;sel.appendChild(o);
  });
}

// ── Play ─────────────────────────────────────────────────────────────────────
function playCard(event,btn){
  event.stopPropagation();
  const idx=parseInt(btn.dataset.idx);
  const m=getList()[idx];
  if(m&&m.file) startPlay(m.file,m.key);
}

function startPlay(path,key){
  let url='/api/play?path='+encodeURIComponent(path);
  if(key) url+='&key='+encodeURIComponent(key);
  fetch(url)
    .then(r=>r.json())
    .then(d=>{
      if(!d.ok){ showToast('⚠ '+d.error,true); return; }
      if(key){
        const today=new Date().toISOString().slice(0,10);
        const item=allMovies.find(x=>x.key===key)||allSeries.find(x=>x.key===key);
        if(item){
          item.last_played=today;
          if(document.getElementById('sortBy').value==='watched') render();
        }
      }
    })
    .catch(()=>showToast('⚠ Verbindungsfehler',true));
}

// "Auf Gerät wiedergeben" (DLNA-Cast auf den LG-Fernseher) - siehe cast_to_lg
// im Backend. Sucht das Gerät per SSDP im Netzwerk, kann daher ein paar
// Sekunden dauern, wenn noch nichts gecacht ist.
async function castToLgTv(btn){
  const key=btn.dataset.key;
  if(!key) return;
  const original=btn.innerHTML;
  btn.innerHTML='<i class="fa-solid fa-tv"></i> Suche Fernseher…';
  btn.style.pointerEvents='none';
  try{
    const d=await fetch('/api/tv-cast',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key})}).then(r=>r.json());
    if(d.ok) showToast('▶ Wiedergabe gestartet auf '+(d.device||'Fernseher'));
    else showToast('⚠ '+(d.error||'Fehler'),true);
  }catch{
    showToast('⚠ Netzwerkfehler',true);
  }finally{
    btn.innerHTML=original;
    btn.style.pointerEvents='';
  }
}

function showToast(msg,err=false,undoFn=null){
  const t=document.getElementById('toast');
  t.innerHTML='';
  const span=document.createElement('span');
  span.textContent=msg;
  t.appendChild(span);
  if(undoFn){
    const btn=document.createElement('button');
    btn.className='toast-undo';
    btn.textContent='Rückgängig';
    btn.onclick=()=>{ t.style.opacity='0'; t.style.pointerEvents='none'; clearTimeout(t._t); undoFn(); };
    t.appendChild(btn);
  }
  t.style.color=err?'#e74c3c':'var(--text)';
  t.style.opacity='1';
  t.style.pointerEvents=undoFn?'auto':'none';
  clearTimeout(t._t);
  t._t=setTimeout(()=>{t.style.opacity='0';t.style.pointerEvents='none';},undoFn?6000:3000);
}

// ── Modal ─────────────────────────────────────────────────────────────────────
// Zufälliger Titel aus der AKTUELL gefilterten Liste (Suche/Genre/Tab bleiben
// berücksichtigt) - für "was schaue ich heute" statt endlos scrollen.
function pickRandom(){
  const list=getList();
  if(!list.length){ showToast('⚠ Keine Titel für die aktuellen Filter',true); return; }
  openModal(Math.floor(Math.random()*list.length));
}

function openModal(idx, fromDiary){
  const m=getList()[idx];
  if(!m)return;
  openModalForItem(m, currentTab==='movies'?'movie':'series', fromDiary);
  modalGridIdx=idx;
}

// ←/→ im offenen Modal zum vorherigen/nächsten Titel der aktuellen Grid-Liste
// (nur wenn das Modal übers Grid geöffnet wurde, siehe modalGridIdx)
function modalNav(dir){
  if(modalGridIdx===null) return;
  const list=getList();
  const idx=modalGridIdx+dir;
  if(idx<0||idx>=list.length) return;
  openModal(idx);
}

// ── Beschreibung/Meta-Section einklappen ────────────────────────────────────
// Bei Serien startet die ganze Section (Beschreibung+Regie/Laufzeit/etc.)
// immer eingeklappt - Episoden/Bewertung sind dort meist wichtiger. Bei
// Filmen nur, wenn die Beschreibung tatsächlich mehr als 2 Zeilen belegt
// (per Messung, da line-clamp allein kein zuverlässiges "brauche ich das
// hier überhaupt" liefert - eine kurze Beschreibung soll direkt sichtbar
// bleiben, ohne unnötigen Pfeil).
function setupInfoCollapse(isSeries){
  const info=document.getElementById('m-info');
  const plot=document.getElementById('m-plot');
  info.classList.remove('collapsible','collapsed');
  document.getElementById('m-info-toggle-label').textContent='Mehr anzeigen';
  if(isSeries){
    info.classList.add('collapsible','collapsed');
    return;
  }
  // Erst normal (ungeklappt) rendern lassen, dann nach dem nächsten Frame
  // die tatsächliche Höhe der Beschreibung messen.
  requestAnimationFrame(()=>{
    const lh=parseFloat(getComputedStyle(plot).lineHeight)||20;
    if(plot.scrollHeight > lh*2+2){
      info.classList.add('collapsible','collapsed');
    }
  });
}

function toggleInfo(){
  const info=document.getElementById('m-info');
  const collapsed=info.classList.toggle('collapsed');
  document.getElementById('m-info-toggle-label').textContent=
    collapsed?'Mehr anzeigen':'Weniger anzeigen';
}

// Öffnet das Bewertungs-/Detail-Modal für ein beliebiges Objekt (Bibliotheks-
// Karte oder Game, das nicht in einem Grid liegt, z.B. beim Spiel-Hinzufügen
// oder beim Öffnen eines Games-Tagebucheintrags). fromDiary=true blendet den
// Abspielen-Button aus (im Tagebuch-Kontext nicht gewünscht).
function openModalForItem(m, kind, fromDiary){
  if(!m)return;
  // Nur openModal() (Aufruf übers Grid) setzt modalGridIdx direkt im Anschluss
  // wieder korrekt - jeder andere Aufrufpfad (Tagebuch-Stub, Spiele, o.ä.)
  // deaktiviert die ←/→-Navigation, da dort kein sinnvoller Grid-Index existiert.
  modalGridIdx=null;
  const isMov=kind==='movie', isSer=kind==='season'||kind==='series', isGame=kind==='game';
  const icon=isSer?'S':isGame?'G':'F';
  modalItem={...m, kind:isMov?'movie':isSer?'series':'game'};
  modalSeason=isSer?1:null;

  document.getElementById('m-poster').innerHTML=m.poster
    ?`<img src="${m.poster}" alt="${esc(m.t)}">`
    :`<div class="mthumb-ph">${icon}</div>`;
  document.getElementById('m-title').textContent=m.t||'—';

  let ys=m.y||'';
  if(m.seasons)ys+=` · ${m.seasons} Staffel${+m.seasons!==1?'n':''}`;
  if(isGame&&m.platforms&&m.platforms.length) ys+=` · ${m.platforms.join(', ')}`;
  if(m.rated)  ys+=` · ${m.rated==='Not Rated'?'Ohne Altersfreigabe':m.rated}`;
  document.getElementById('m-year').textContent=ys;

  const rEl=document.getElementById('m-rating');
  rEl.className='mrating '+rCls(m.rating);
  rEl.innerHTML=m.rating
    ?`<span class="rb-src">${m.rating_source==='tmdb'?'TMDB':'IMDb'}</span> ${m.rating.toFixed(1)} <span>/ 10${m.votes?' · '+m.votes+' Stimmen':''}</span>`
    :`<span style="font-size:13px;color:var(--muted)">Keine Bewertung</span>`;

  document.getElementById('m-genres').innerHTML=
    (m.genre||'').split(',').map(g=>g.trim()).filter(Boolean)
      .map(g=>`<span class="gp">${esc(genreLabel(g))}</span>`).join('');
  document.getElementById('m-plot').textContent=m.plot||'Keine Beschreibung verfügbar.';

  const metaItems=[
    isMov&&m.director?['Regie',m.director]:null,
    m.actors  ?['Besetzung',m.actors]  :null,
    m.runtime ?[isMov?'Laufzeit':'Episode',m.runtime]:null,
    m.country ?['Land',m.country]      :null,
    m.language?['Sprache',m.language]  :null,
    m.awards&&m.awards!=='N/A.'?['Auszeichnungen',m.awards]:null,
  ].filter(Boolean);
  document.getElementById('m-meta').innerHTML=metaItems
    .map(([l,v])=>`<div class="mi2"><span class="ml">${l}</span><span class="mv">${esc(v)}</span></div>`)
    .join('');

  // Beschreibung+Meta einklappen: bei Serien immer, bei Filmen nur wenn die
  // Beschreibung tatsächlich länger als 2 Zeilen ist (per Messung nach dem
  // Rendern, da das vom Modal-Layout/Zeilenumbruch abhängt).
  setupInfoCollapse(isSer);

  // Links – play-Button über data-Attribut, kein Pfad im onclick
  const linksEl=document.getElementById('m-links');
  linksEl.innerHTML='';

  if(isGame){
    const gq=encodeURIComponent(m.t);
    const ig=document.createElement('a');
    ig.className='mlink lb'; ig.href=`https://www.igdb.com/search?type=1&q=${gq}`;
    ig.target='_blank'; ig.textContent='IGDB';
    linksEl.appendChild(ig);

    const db=document.createElement('span');
    db.className='mlink del-btn';
    db.textContent='Entfernen';
    db.onclick=function(){askDelGame(m);};
    linksEl.appendChild(db);
  } else {
    const q2=encodeURIComponent(m.t+(m.y?' '+m.y.slice(0,4):''));
    const lbUrl=isMov?`https://letterboxd.com/search/films/${q2}/`:`https://letterboxd.com/search/${q2}/`;
    const imdbUrl=m.imdb_id?`https://www.imdb.com/title/${m.imdb_id}/`:null;

    if(m.file){
      if(!fromDiary){
        const pb=document.createElement('span');
        pb.className='mlink play-btn';
        pb.textContent='▶ Abspielen';
        pb.dataset.path=m.file;
        pb.dataset.key=m.key||'';
        pb.onclick=function(){startPlay(this.dataset.path,this.dataset.key);};
        linksEl.appendChild(pb);

        // Nur bei HDR-Dateien: Wiedergabe auf dem LG-Fernseher (DLNA-Cast,
        // entspricht "Wiedergabe auf Gerät" im Windows-Explorer-Kontextmenü).
        if(isMov&&m.hdr){
          const tb=document.createElement('span');
          tb.className='mlink';
          tb.dataset.key=m.key||'';
          tb.innerHTML='<i class="fa-solid fa-tv"></i> Auf LG TV abspielen';
          tb.onclick=function(){castToLgTv(this);};
          linksEl.appendChild(tb);
        }
      }

      // Löschen (nur Filme, nicht Serien)
      if(isMov){
        const db=document.createElement('span');
        db.className='mlink del-btn';
        db.textContent='Löschen';
        db.dataset.path=m.file;
        db.dataset.key=m.key;
        db.onclick=function(){askDelMovie(this,m);};
        linksEl.appendChild(db);
      }
    }
    if(imdbUrl){
      const ia=document.createElement('a');
      ia.className='mlink imdb'; ia.href=imdbUrl; ia.target='_blank'; ia.textContent='IMDb';
      linksEl.appendChild(ia);
    }
    const lb=document.createElement('a');
    lb.className='mlink lb'; lb.href=lbUrl; lb.target='_blank'; lb.textContent='Letterboxd';
    linksEl.appendChild(lb);

    const cb=document.createElement('span');
    cb.className='mlink';
    cb.textContent='Cover wählen';
    cb.onclick=function(){
      openPosterPicker({key:m.key||'', kind:isMov?'movie':'series', title:m.t, year:m.y});
    };
    linksEl.appendChild(cb);
  }

  // Episodenliste (nur Serien, und nur wenn lokal in der Bibliothek vorhanden -
  // sonst überschreibt die leere Episodenantwort die aus dem Tagebuch bekannte
  // Staffel wieder mit Staffel 1, siehe _noLibrary-Stub in openFromDiary)
  const epSec=document.getElementById('m-ep-section');
  if(isSer&&m.key&&!m._noLibrary){
    epSec.style.display='block';
    document.getElementById('m-ep-list').innerHTML='<div class="ep-loading">Lade Episoden…</div>';
    loadEpisodes(m.key);
  } else { epSec.style.display='none'; }

  renderRateBox();

  document.getElementById('mo').classList.add('open');
  document.body.style.overflow='hidden';
}

// ── Episoden ──────────────────────────────────────────────────────────────────
async function loadEpisodes(key){
  try{
    const data=await fetch('/api/episodes?key='+encodeURIComponent(key)).then(r=>r.json());
    const eps=data.episodes||[];
    // Verfügbare Staffeln fürs Bewertungs-Dropdown
    if(modalItem&&modalItem.key===key){
      // Alle Staffeln der Serie (laut Metadaten), nicht nur die mit Dateien vor Ort
      const set=new Set(eps.map(e=>e.season).filter(s=>s>0));
      const total=parseInt(modalItem.seasons)||0;
      for(let s=1;s<=total;s++) set.add(s);
      diary.filter(d=>d.key===key&&d.kind==='season'&&d.season>0).forEach(d=>set.add(d.season));
      const seasons=[...set].sort((a,b)=>a-b);
      modalItem._seasons=seasons.length?seasons:[1];
      if(!modalItem._seasons.includes(modalSeason)) modalSeason=modalItem._seasons[0];
      renderRateBox();
    }
    renderEpisodes(eps,key);
  }catch{
    document.getElementById('m-ep-list').innerHTML='<div class="ep-loading" style="color:var(--red)">Fehler beim Laden.</div>';
  }
}

function renderEpisodes(eps,seriesKey){
  const cont=document.getElementById('m-ep-list');
  if(!eps.length){
    // Alle Folgen gelöscht, Ordner aber noch da (0 Bytes an Videos mehr) - ohne
    // diesen Hinweis bliebe die Serie für immer als leere Karteileiche in der
    // Bibliothek stehen, weil refresh() einen noch existierenden Ordner nie
    // von sich aus entfernt.
    const rated=diary.some(e=>e.key===seriesKey&&e.kind==='season');
    cont.innerHTML=`<div class="dempty" style="padding:22px 10px">
      <div style="font-size:15px;margin-bottom:6px">Keine Folgen mehr gefunden.</div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:16px">
        Serie komplett durchgeschaut und die Dateien schon gelöscht?
        ${rated?'<br>Dein Tagebuch-Eintrag bleibt beim Entfernen erhalten.':'<br>Du kannst sie entfernen, ohne sie zu bewerten.'}
      </div>
      <button class="btn" id="ep-remove-series" style="margin:0 auto">
        <i class="fa-solid fa-trash-can"></i> Serie aus Bibliothek entfernen
      </button>
    </div>`;
    const rmBtn=document.getElementById('ep-remove-series');
    rmBtn.onclick=()=>{
      // Inline-Nachfrage statt confirm()-Popup
      rmBtn.outerHTML=`<div id="ep-remove-ask" style="display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap">
        <span style="font-size:14px">Serie wirklich entfernen?</span>
        <button class="btn" id="ep-rm-yes">Ja</button>
        <button class="btn" id="ep-rm-no">Nein</button></div>`;
      document.getElementById('ep-rm-yes').onclick=()=>removeSeriesFromLibrary(seriesKey);
      document.getElementById('ep-rm-no').onclick=()=>renderEpisodes(eps,seriesKey);
    };
    return;
  }

  const seasons={};
  eps.forEach(ep=>{const s=ep.season||0;if(!seasons[s])seasons[s]=[];seasons[s].push(ep);});
  const keys=Object.keys(seasons).map(Number).sort((a,b)=>a-b);
  const multi=keys.length>1||(keys.length===1&&keys[0]>0);

  // Episoden in DOM-Elemente umwandeln (kein string-HTML mit Pfaden).
  // Staffelweise einklappbar: die zuletzt geöffnete/aktuelle Staffel
  // (modalSeason) startet aufgeklappt, alle anderen eingeklappt.
  const frag=document.createDocumentFragment();
  // Aufgeklappt: die gewählte Staffel - gibt es zu ihr keine Dateien, die erste vorhandene
  const openSeason=keys.includes(modalSeason)?modalSeason:(keys.find(k=>k>0)??keys[0]);
  keys.forEach(s=>{
    let rowsWrap=null;
    if(multi&&s>0){
      const isOpen=s===openSeason;
      const hdr=document.createElement('div');
      hdr.className='ep-season'+(isOpen?' open':'');
      hdr.innerHTML=`<i class="fa-solid fa-chevron-down"></i><span>Staffel ${s}</span>`;
      rowsWrap=document.createElement('div');
      rowsWrap.className='ep-season-rows'+(isOpen?' open':'');
      hdr.onclick=()=>{
        hdr.classList.toggle('open');
        rowsWrap.classList.toggle('open');
      };
      frag.appendChild(hdr);
      frag.appendChild(rowsWrap);
    }
    seasons[s].forEach(ep=>{
      const row=document.createElement('div');
      row.className='ep-row';

      const label=document.createElement('span');
      label.className='ep-label'; label.textContent=ep.label||'—';

      const name=document.createElement('span');
      name.className='ep-name'; name.textContent=ep.name||ep.label;
      name.title=ep.path;

      const actions=document.createElement('div');
      actions.className='ep-actions';
      buildEpActions(actions,ep.path,row,seriesKey,ep.label);

      row.appendChild(label); row.appendChild(name); row.appendChild(actions);
      (rowsWrap||frag).appendChild(row);
    });
  });
  cont.innerHTML='';
  cont.appendChild(frag);
}

function buildEpActions(actions,path,row,seriesKey,epLabel){
  actions.innerHTML='';
  const pb=document.createElement('button');
  pb.className='ep-btn ep-play'; pb.textContent='▶';
  pb.onclick=()=>startPlay(path,seriesKey);

  const rb=document.createElement('button');
  rb.className='ep-btn ep-reddit';
  rb.title='Reddit-Diskussion suchen (Google)';
  rb.innerHTML='<i class="fa-brands fa-reddit-alien"></i>';
  rb.onclick=()=>openEpisodeDiscussion(epLabel);

  const db=document.createElement('button');
  db.className='ep-btn ep-del'; db.textContent='✕'; db.title='Löschen';
  db.onclick=()=>askDel(actions,path,row,seriesKey,epLabel);

  actions.appendChild(pb); actions.appendChild(rb); actions.appendChild(db);
}

// Sucht die Episoden-Diskussion nicht direkt auf Reddit (kein zuverlässiger
// API-Zugriff ohne Auth nötig), sondern per Google-Suche mit site:reddit.com -
// findet praktisch immer den richtigen Episode-Discussion-Thread, egal wie
// das jeweilige Subreddit seine Threads benennt.
function openEpisodeDiscussion(epLabel){
  const title=(modalItem&&modalItem.t)||'';
  const q=`${title} ${epLabel||''} discussion site:reddit.com`;
  window.open('https://www.google.com/search?q='+encodeURIComponent(q),'_blank');
}

function askDel(actions,path,row,seriesKey,epLabel){
  actions.innerHTML='';
  const txt=document.createElement('span');
  txt.className='ep-confirm-txt'; txt.textContent='Löschen?';

  const yes=document.createElement('button');
  yes.className='ep-btn ep-yes'; yes.textContent='Ja';
  yes.onclick=()=>doDelete(path,row,actions,seriesKey,epLabel);

  const no=document.createElement('button');
  no.className='ep-btn'; no.textContent='Nein';
  no.onclick=()=>buildEpActions(actions,path,row,seriesKey,epLabel);

  actions.appendChild(txt); actions.appendChild(yes); actions.appendChild(no);
}

async function doDelete(path,row,actions,seriesKey,epLabel){
  try{
    const d=await fetch('/api/file?path='+encodeURIComponent(path),{method:'DELETE'}).then(r=>r.json());
    if(d.ok){
      row.classList.add('removing');
      setTimeout(()=>row.remove(),350);
      showToast('Gelöscht');
    } else {
      showToast('⚠ '+d.error,true);
      buildEpActions(actions,path,row,seriesKey,epLabel);
    }
  }catch{
    showToast('⚠ Netzwerkfehler',true);
    buildEpActions(actions,path,row,seriesKey,epLabel);
  }
}

// ── Sterne-Widget (halbe Sterne, Letterboxd-Stil) ─────────────────────────────
function starsHTML(rating,small){
  let h=`<span class="stars${small?' stars-sm':''}">`;
  for(let i=1;i<=5;i++){
    const cls=rating>=i?'on':(rating>=i-0.5?'half':'');
    h+=`<span class="star ${cls}" data-v="${i}">★</span>`;
  }
  return h+'</span>';
}

function bindStars(el,onPick){
  el.querySelectorAll('.star').forEach(st=>{
    st.onmousemove=e=>{
      const v=+st.dataset.v;
      const half=(e.offsetX < st.offsetWidth/2);
      el.querySelectorAll('.star').forEach(s=>{
        const sv=+s.dataset.v;
        s.classList.remove('hv','hvh');
        if(sv<v||(sv===v&&!half)) s.classList.add('hv');
        else if(sv===v&&half)     s.classList.add('hvh');
      });
    };
    st.onclick=e=>{
      const v=+st.dataset.v;
      const half=(e.offsetX < st.offsetWidth/2);
      onPick(half?v-0.5:v);
    };
  });
  el.onmouseleave=()=>el.querySelectorAll('.star').forEach(s=>s.classList.remove('hv','hvh'));
}

// ── Bewertungs-Formular im Modal ──────────────────────────────────────────────
function latestOf(matches){
  // Bei mehreren Einträgen (z.B. Rewatches) den zuletzt geschauten nehmen,
  // nicht einfach den ersten Treffer in der Speicher-Reihenfolge (die einer
  // Import-Reihenfolge entsprechen kann und nichts mit dem Datum zu tun hat).
  if(!matches.length) return undefined;
  return matches.reduce((best,e)=>{
    if(!best) return e;
    const bd=best.date||'', ed=e.date||'';
    if(ed!==bd) return ed>bd?e:best;
    return (e.created||0)>(best.created||0)?e:best;
  }, undefined);
}
function findEntry(key,season,kind){
  // modalItem.kind ist bei Serien 'series' (Anzeige-Kind), im Tagebuch werden
  // Staffel-Einträge aber unter kind:'season' gespeichert - beides abdecken.
  if(kind==='series'||kind==='season')
    return latestOf(diary.filter(e=>e.key===key&&e.kind==='season'&&e.season===season));
  return latestOf(diary.filter(e=>e.key===key&&e.kind===(kind||'movie')));
}

function renderRateBox(){
  const box=document.getElementById('m-rate');
  const m=modalItem;
  if(!m){ box.innerHTML=''; return; }
  const isSer=(m.kind==='series');
  const isGame=(m.kind==='game');
  const existing=findEntry(m.key,isSer?modalSeason:null,m.kind);
  const today=new Date().toISOString().slice(0,10);

  let seasonSel='';
  if(isSer){
    const opts=(m._seasons||[]).map(s=>
      `<option value="${s}"${s===modalSeason?' selected':''}>Staffel ${s}</option>`).join('');
    seasonSel=`<select class="season-pick" id="rt-season">${opts||'<option value="1">Staffel 1</option>'}</select>`;
  }

  const dateRow=isGame
    ?`<div class="rate-row">
        <input type="date" id="rt-start" title="Start" value="${existing&&existing.startDate?existing.startDate:''}">
        <input type="date" id="rt-finish" title="Ende" value="${existing?(existing.finishDate||''):''}">
        <label class="rw-toggle">
          <input type="checkbox" id="rt-rw"${existing&&existing.rewatch?' checked':''}> Replay
        </label>
      </div>
      <div class="rate-row">
        <input type="text" id="rt-platform" placeholder="Plattform (optional)"
               value="${esc(existing?(existing.platform||''):((m.platforms&&m.platforms[0])||''))}">
      </div>`
    :`<div class="rate-row">
        <input type="date" id="rt-date" value="${existing?existing.date:today}">
        <label class="rw-toggle">
          <input type="checkbox" id="rt-rw"${existing&&existing.rewatch?' checked':''}> Erneut gesehen
        </label>
      </div>`;

  box.innerHTML=`
    <div class="rate-head">
      <span class="rate-lbl">${isSer?'Staffel bewerten':isGame?'Spiel bewerten':'Film bewerten'}</span>
      ${existing?`<span class="logged-hint">✓ Eingetragen am ${fmtDate(existing.date)}</span>`:''}
    </div>
    <div class="rate-row">
      ${seasonSel}
      <span id="rt-stars">${starsHTML(existing?existing.rating:0)}</span>
      <span class="rate-lbl" id="rt-val">${existing&&existing.rating?existing.rating.toFixed(1):'–'}</span>
    </div>
    ${dateRow}
    ${isSer?`<div class="rate-row">
      <label class="rw-toggle">
        <input type="checkbox" id="rt-mini"${existing&&existing.mini?' checked':''}> Mini-Serie
      </label>
      <select class="season-pick" id="rt-status">
        <option value="">Status: automatisch${existing?' ('+(STATUS_LBL[seriesStatus(existing)]||'–')+')':''}</option>
        <option value="started"  ${existing&&existing.statusOverride==='started'?'selected':''}>Begonnen</option>
        <option value="continued"${existing&&existing.statusOverride==='continued'?'selected':''}>Fortgesetzt</option>
        <option value="completed"${existing&&existing.statusOverride==='completed'?'selected':''}>Abgeschlossen</option>
      </select>
    </div>`:''}
    <div class="rate-row">
      <textarea id="rt-comment" placeholder="Notiz (optional)">${esc(existing?existing.comment||'':'')}</textarea>
    </div>
    ${existing?`<div class="noms-box">
      <div class="rate-lbl">Nominierungen ${existing.date?'· '+existing.date.slice(0,4):''}</div>
      <div class="noms-chips" id="rt-noms"></div>
    </div>`:''}
    <div class="rate-row" style="justify-content:flex-end">
      ${existing?'<button class="rate-clear" id="rt-del">Eintrag löschen</button>':''}
      <button class="rate-save" id="rt-save">${existing?'Aktualisieren':'Ins Tagebuch'}</button>
    </div>`;

  // Nominierungs-Chips (nur zulässige Kategorien)
  if(existing){
    const catsSrc=isSer?CATS_SERIES:isGame?CATS_GAMES:CATS_FILM;
    const cats=catsSrc.filter(c=>eligible(existing,c.id));
    renderNomChips(box.querySelector('#rt-noms'), existing, cats, ()=>renderRateBox());
  }

  let picked=existing?existing.rating:0;
  function attachStars(){
    bindStars(box.querySelector('#rt-stars .stars'),v=>{
      picked=v;
      box.querySelector('#rt-stars').innerHTML=starsHTML(v);
      box.querySelector('#rt-val').textContent=v.toFixed(1);
      attachStars();
    });
  }
  attachStars();

  const seasonEl=box.querySelector('#rt-season');
  if(seasonEl) seasonEl.onchange=()=>{ modalSeason=+seasonEl.value; renderRateBox(); };

  box.querySelector('#rt-save').onclick=async()=>{
    // Bei neuen Einträgen ist eine Bewertung Pflicht; beim Bearbeiten eines
    // bestehenden Eintrags (z.B. nur um die Notiz zu ändern/entfernen) nicht -
    // sonst lässt sich ein unbewerteter Eintrag nicht mehr bearbeiten.
    if(!picked && !existing){ showToast('⚠ Bitte erst eine Bewertung wählen',true); return; }
    const payload={
      id:      existing?existing.id:null,
      key:     m.key,
      kind:    isSer?'season':isGame?'game':'movie',
      title:   m.t,
      year:    (m.y||'').toString().slice(0,4),
      season:  isSer?modalSeason:null,
      poster:  m.poster,
      genre:   m.genre||'',
      rating:  picked||null,
      comment: box.querySelector('#rt-comment').value,
      rewatch: box.querySelector('#rt-rw').checked,
      mini:    isSer?box.querySelector('#rt-mini').checked:false,
      statusOverride: isSer?(box.querySelector('#rt-status').value||null):null,
    };
    if(isGame){
      const start=box.querySelector('#rt-start').value||null;
      const finish=box.querySelector('#rt-finish').value||null;
      payload.startDate=start; payload.finishDate=finish;
      payload.date=finish||start||today;
      payload.platform=box.querySelector('#rt-platform').value.trim();
    } else {
      payload.date=box.querySelector('#rt-date').value;
    }
    await saveDiaryEntry(payload);
  };

  const delBtn=box.querySelector('#rt-del');
  if(delBtn) delBtn.onclick=async()=>{
    await fetch('/api/diary?id='+encodeURIComponent(existing.id),{method:'DELETE'})
      .then(r=>r.json()).then(d=>{ if(d.ok) diary=d.entries; });
    showToast('Eintrag gelöscht'); renderRateBox();
  };
}

async function saveDiaryEntry(payload){
  try{
    const d=await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    }).then(r=>r.json());
    if(d.ok){
      diary=d.entries;
      showToast(payload.id?'Eintrag aktualisiert':'Ins Tagebuch eingetragen');
      if(modalItem) renderRateBox();
      // Grid/Tagebuch/Rückblick live aktualisieren (z.B. Sterne-Durchschnitt auf
      // der Karte), egal ob über das Modal oder das Schnell-Loggen gespeichert wurde.
      refreshVisibleView();
    } else showToast('⚠ '+d.error,true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

// ── Schnell-Loggen direkt von der Karte (nur Filme) ───────────────────────────
let _quickRatePop=null;
function closeQuickRate(){
  if(_quickRatePop){ _quickRatePop.remove(); _quickRatePop=null; }
  document.removeEventListener('click',_quickRateOutsideHandler);
}
function _quickRateOutsideHandler(e){
  if(_quickRatePop && !_quickRatePop.contains(e.target)) closeQuickRate();
}
function openQuickRate(event,btn){
  event.stopPropagation();
  closeQuickRate();
  const idx=+btn.dataset.idx;
  const m=getList()[idx];
  if(!m) return;
  const existing=findEntry(m.key,null,'movie');

  const pop=document.createElement('div');
  pop.className='quick-rate-pop';
  pop.innerHTML=`<div class="quick-rate-lbl">${esc(m.t)}</div>
    <span id="qr-stars">${starsHTML(existing?existing.rating:0)}</span>`;
  document.body.appendChild(pop);

  const r=btn.getBoundingClientRect();
  pop.style.top=Math.round(r.bottom+8)+'px';
  pop.style.left=Math.round(Math.min(r.left,window.innerWidth-240))+'px';
  _quickRatePop=pop;
  setTimeout(()=>document.addEventListener('click',_quickRateOutsideHandler),0);

  bindStars(pop.querySelector('#qr-stars .stars'),async v=>{
    pop.querySelector('#qr-stars').innerHTML=starsHTML(v);
    const today=new Date().toISOString().slice(0,10);
    await saveDiaryEntry({
      id:      existing?existing.id:null,
      key:     m.key, kind:'movie', title:m.t,
      year:    (m.y||'').toString().slice(0,4),
      season:  null, poster:m.poster, genre:m.genre||'',
      rating:  v,
      comment: existing?(existing.comment||''):'',
      rewatch: existing?!!existing.rewatch:false,
      date:    existing?existing.date:today,
    });
    closeQuickRate();
  });
}

function fmtDate(s){
  if(!s)return '';
  const p=s.split('-');
  return p.length===3?`${p[2]}.${p[1]}.${p[0]}`:s;
}

// ── Film löschen (aus Modal) ──────────────────────────────────────────────────
function askDelMovie(btn,m){
  const linksEl=document.getElementById('m-links');
  // Bestätigungszeile einblenden
  linksEl.querySelectorAll('.del-confirm,.del-cancel,.del-confirm-txt').forEach(el=>el.remove());
  btn.style.display='none';

  const txt=document.createElement('span');
  txt.className='del-confirm-txt';
  txt.style.cssText='font-size:12px;color:var(--muted);align-self:center';
  txt.textContent='Datei wirklich löschen?';

  const yes=document.createElement('span');
  yes.className='mlink del-confirm';
  yes.textContent='Ja, löschen';
  yes.onclick=()=>doDelMovie(m);

  const no=document.createElement('span');
  no.className='mlink del-cancel';
  no.textContent='Abbrechen';
  no.onclick=()=>{btn.style.display='';txt.remove();yes.remove();no.remove();};

  linksEl.appendChild(txt); linksEl.appendChild(yes); linksEl.appendChild(no);
}

async function doDelMovie(m){
  try{
    const d=await fetch('/api/file?path='+encodeURIComponent(m.file)+'&key='+encodeURIComponent(m.key),{method:'DELETE'}).then(r=>r.json());
    if(d.ok){
      closeModal();
      allMovies=allMovies.filter(x=>x.key!==m.key);
      render();
      showToast('Film gelöscht');
    } else { showToast('⚠ '+d.error,true); }
  } catch { showToast('⚠ Netzwerkfehler',true); }
}

function closeModal(){document.getElementById('mo').classList.remove('open');document.body.style.overflow='';}
function bgClose(e){if(e.target===document.getElementById('mo'))closeModal();}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(document.getElementById('actorModal').classList.contains('open')) closeActorPicker();
    else if(document.getElementById('posterModal').classList.contains('open')) closePosterPicker();
    else if(document.getElementById('impModal').classList.contains('open')) closeImport();
    else if(document.getElementById('lbModal').classList.contains('open')) closeLbModal();
    else closeModal();
    return;
  }

  // Restliche Shortcuts nicht auslösen, während in einem Eingabefeld getippt
  // wird (Suche, Kommentar-Textarea, Formulare, o.ä.) - sonst würde z.B. "1"
  // im Suchfeld ungewollt den Tab wechseln statt eine Ziffer einzugeben.
  const tag=(e.target.tagName||'').toLowerCase();
  const typing=tag==='input'||tag==='textarea'||tag==='select'||e.target.isContentEditable;
  if(typing) return;

  if(e.key==='/'){
    e.preventDefault();
    document.getElementById('search').focus();
    return;
  }
  if(e.key>='1'&&e.key<='5'&&TABS[+e.key-1]){
    switchTab(TABS[+e.key-1]);
    return;
  }
  if(document.getElementById('mo').classList.contains('open')){
    if(e.key==='ArrowRight') modalNav(1);
    else if(e.key==='ArrowLeft') modalNav(-1);
  }
});

// ── Manuelle Cover-Auswahl ──────────────────────────────────────────────────────
let posterPickTarget=null;

function openPosterPicker(target){
  posterPickTarget=target;
  document.getElementById('posterModalSub').textContent=
    (target.title||'')+(target.year?' · '+(''+target.year).slice(0,4):'');
  const body=document.getElementById('posterModalBody');
  body.innerHTML='<div class="ep-loading">Suche Cover…</div>';
  document.getElementById('posterModal').classList.add('open');
  document.body.style.overflow='hidden';

  const q=`title=${encodeURIComponent(target.title||'')}&year=${encodeURIComponent((target.year||'').toString())}&kind=${target.kind==='series'?'series':'movie'}`;
  fetch('/api/poster/candidates?'+q).then(r=>r.json()).then(d=>{
    renderPosterCandidates(d.candidates||[]);
  }).catch(()=>{
    body.innerHTML='<div class="dempty">⚠ Suche fehlgeschlagen (keine Verbindung zu TMDB?)</div>';
  });
}

function renderPosterCandidates(candidates){
  const body=document.getElementById('posterModalBody');
  if(!candidates.length){
    body.innerHTML='<div class="dempty">Keine Cover gefunden.</div>';
    return;
  }
  body.innerHTML=`<div class="poster-grid">${candidates.map((c,i)=>`
    <div class="poster-pick" data-i="${i}">
      <img src="${c.url}" alt="" loading="lazy">
      <div class="poster-pick-lbl">${esc(c.label||'')}</div>
    </div>`).join('')}</div>`;
  body.querySelectorAll('.poster-pick').forEach(el=>{
    el.onclick=()=>choosePoster(candidates[+el.dataset.i].url);
  });
}

async function choosePoster(url){
  if(!posterPickTarget)return;
  const body=document.getElementById('posterModalBody');
  try{
    const d=await fetch('/api/poster/choose',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        key: posterPickTarget.key||'',
        entryId: posterPickTarget.entryId||'',
        url,
      })
    }).then(r=>r.json());
    if(!d.ok){ showToast('⚠ '+(d.error||'Fehler'),true); return; }

    // Cache-Busting, sonst zeigt der Browser das alte Bild unter derselben URL
    const busted=d.poster+'?v='+Date.now();

    if(posterPickTarget.key){
      const item=allMovies.find(x=>x.key===posterPickTarget.key)
               || allSeries.find(x=>x.key===posterPickTarget.key);
      if(item) item.poster=busted;
      if(modalItem&&modalItem.key===posterPickTarget.key) modalItem.poster=busted;
    }
    diary=d.entries;
    if(modalItem&&(modalItem.key===posterPickTarget.key||(posterPickTarget.entryId))){
      document.getElementById('m-poster').innerHTML=`<img src="${busted}" alt="">`;
    }
    closePosterPicker();
    if(currentTab==='movies'||currentTab==='series') render();
    if(currentTab==='diary') renderDiary();
    showToast('Cover aktualisiert');
  }catch{
    body.innerHTML='<div class="dempty">⚠ Netzwerkfehler</div>';
  }
}

function closePosterPicker(){
  document.getElementById('posterModal').classList.remove('open');
  const mOpen=document.getElementById('mo').classList.contains('open');
  document.body.style.overflow=mOpen?'hidden':'';
}

// ── Titel korrigieren (falsch erkannter Film/Serie) ───────────────────────────
let tfTarget=null, tfCandidates=[], tfSelected=null;

function openTitleFix(event,btn){
  event.stopPropagation();
  const idx=+btn.dataset.idx;
  const m=getList()[idx];
  if(!m) return;
  tfTarget={path:m.file||m.folder, kind:currentTab==='movies'?'movie':'series'};
  tfSelected=null;
  document.getElementById('tfCurrent').textContent=(m.t||'')+(m.y?' · '+m.y:'');
  document.getElementById('tfQuery').value=m.t||'';
  document.getElementById('titleFixModal').classList.add('open');
  document.body.style.overflow='hidden';
  searchTitleFix();
}

async function removeSeriesFromLibrary(key){
  const rated=diary.some(e=>e.key===key&&e.kind==='season');
  try{
    const d=await fetch('/api/library?key='+encodeURIComponent(key),{method:'DELETE'}).then(r=>r.json());
    if(!d.ok){ showToast('⚠ '+(d.error||'Fehler'),true); return; }
    allMovies=d.data.movies||[]; allSeries=d.data.series||[];
    closeModal();
    render();
    showToast('✓ Serie entfernt');
  }catch{
    showToast('⚠ Netzwerkfehler',true);
  }
}

function closeTitleFix(){
  document.getElementById('titleFixModal').classList.remove('open');
  const mOpen=document.getElementById('mo').classList.contains('open');
  document.body.style.overflow=mOpen?'hidden':'';
}

async function searchTitleFix(){
  if(!tfTarget) return;
  const q=document.getElementById('tfQuery').value.trim();
  const results=document.getElementById('tfResults');
  if(!q){ results.innerHTML='<div class="dempty">Suchbegriff eingeben.</div>'; return; }
  results.innerHTML='<div class="ep-loading">Suche…</div>';
  try{
    const d=await fetch(`/api/title-search?q=${encodeURIComponent(q)}&kind=${tfTarget.kind}`).then(r=>r.json());
    tfCandidates=d.candidates||[];
    renderTitleFixResults();
  }catch{
    results.innerHTML='<div class="dempty">⚠ Suche fehlgeschlagen (keine Verbindung zu TMDB?)</div>';
  }
}

function renderTitleFixResults(){
  const results=document.getElementById('tfResults');
  if(!tfCandidates.length){
    results.innerHTML='<div class="dempty">Keine Treffer.</div>';
    return;
  }
  results.innerHTML=`<div class="tf-list">${tfCandidates.map((c,i)=>`
    <div class="tf-item" data-i="${i}">
      ${c.poster?`<img src="${c.poster}" alt="" loading="lazy">`:'<div class="tf-item-ph"></div>'}
      <div>
        <div><span class="tf-item-title">${esc(c.title)}</span>${c.year?`<span class="tf-item-year">${esc(c.year)}</span>`:''}</div>
        ${c.overview?`<div class="tf-item-overview">${esc(c.overview)}</div>`:''}
      </div>
    </div>`).join('')}</div>
    <div class="tf-apply"><button id="tfApplyBtn" disabled onclick="applyTitleFix()">Übernehmen</button></div>`;
  results.querySelectorAll('.tf-item').forEach(el=>{
    el.onclick=()=>{
      results.querySelectorAll('.tf-item').forEach(x=>x.classList.remove('sel'));
      el.classList.add('sel');
      tfSelected=tfCandidates[+el.dataset.i];
      document.getElementById('tfApplyBtn').disabled=false;
    };
  });
}

async function applyTitleFix(){
  if(!tfTarget||!tfSelected) return;
  const btn=document.getElementById('tfApplyBtn');
  btn.disabled=true; btn.textContent='Speichere…';
  try{
    const d=await fetch('/api/title-override',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({path:tfTarget.path, kind:tfTarget.kind, tmdb_id:tfSelected.tmdb_id})
    }).then(r=>r.json());
    if(!d.ok){ showToast('⚠ '+(d.error||'Fehler'),true); btn.disabled=false; btn.textContent='Übernehmen'; return; }
    allMovies=d.data.movies||[]; allSeries=d.data.series||[];
    closeTitleFix();
    render();
    showToast('✓ Titel korrigiert');
  }catch{
    showToast('⚠ Netzwerkfehler',true);
    btn.disabled=false; btn.textContent='Übernehmen';
  }
}

function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// ── Tagebuch ──────────────────────────────────────────────────────────────────
const MONTHS=['Januar','Februar','März','April','Mai','Juni',
              'Juli','August','September','Oktober','November','Dezember'];
const MSHORT=['Jan','Feb','Mär','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez'];

function setDiaryFilter(f,btn){
  diaryFilter=f;
  diaryYearsShown=1;
  document.querySelectorAll('.dfilter').forEach(b=>b.classList.toggle('active',b===btn));
  renderDiary();
}

function diaryFiltered(){
  let list=[...diary];
  if(diaryFilter!=='all') list=list.filter(e=>e.kind===diaryFilter);
  // Die Kopfzeilen-Suche gilt jetzt tabübergreifend, nicht mehr nur für das
  // Filme/Serien-Grid - dieselbe Eingabe filtert auch das Tagebuch nach Titel.
  const q=(document.getElementById('search').value||'').toLowerCase().trim();
  if(q) list=list.filter(e=>(e.title||'').toLowerCase().includes(q));
  return list.sort((a,b)=>(b.date||'').localeCompare(a.date||'')||(b.created||0)-(a.created||0));
}

// Absteigend sortierte, eindeutige Jahre aus den (undatierten Einträgen -> "Ohne Datum" ganz hinten)
// (nicht zu verwechseln mit diaryYears() weiter unten, das den Jahres-Dropdown im Jahr-Tab befüllt)
function diaryEntryYears(list){
  const ys=new Set();
  let hasNoDate=false;
  list.forEach(e=>{ if(e.date) ys.add(+e.date.slice(0,4)); else hasNoDate=true; });
  const sorted=[...ys].sort((a,b)=>b-a);
  return hasNoDate?[...sorted,null]:sorted;
}

// Nur die Tagebuch-Einträge des laufenden Jahres werden initial gerendert
// (Performance bei mehreren hundert Einträgen) - ältere Jahre lädt ein
// Infinite-Scroll am Ende der Liste nach (siehe bindDiaryLoadMore).
function renderDiary(){
  const full=diaryFiltered();
  const years=diaryEntryYears(full);
  const cont=document.getElementById('diary-list');

  document.getElementById('diary-count').textContent=
    full.length+' Eintr'+(full.length===1?'ag':'äge');
  updateOpenGamesCount();

  if(!full.length){
    const q=document.getElementById('search').value.trim();
    const isFiltered=q||diaryFilter!=='all';
    if(diary.length && isFiltered){
      // Es gibt Einträge, nur Suche/Filter greift gerade zu eng
      cont.innerHTML=`<div class="dempty">Keine Einträge${q?` für „${esc(q)}“`:''}${
        diaryFilter!=='all'?' in diesem Filter':''}.</div>`;
    } else {
      // Tagebuch ist wirklich komplett leer - direkt zum Import anbieten
      cont.innerHTML=`<div class="dempty">
        <div style="font-size:15px;margin-bottom:6px">Noch keine Einträge im Tagebuch.</div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:16px">
          Bewerte einen Film oder eine Staffel, oder importiere dein bestehendes<br>
          Tagebuch von Letterboxd oder Serializd.
        </div>
        <button class="btn" style="margin:0 auto" onclick="openImport()">⬆ Tagebuch importieren</button>
      </div>`;
    }
    return;
  }

  const shownYears=new Set(years.slice(0,diaryYearsShown));
  const list=full.filter(e=>shownYears.has(e.date?+e.date.slice(0,4):null));

  // Spiele mit Start- UND Enddatum: nur zu einem Eintrag kombinieren, wenn
  // dazwischen nichts anderes im Tagebuch steht - sonst zwei Zeilen
  // ("Gestartet" am Start-, "Beendet" am Enddatum). Abgebrochene Spiele
  // bekommen NIE eine zweite/eigene Zeile am Abbruchdatum - nur eine einzige,
  // leichtgewichtige Zeile am Startdatum mit dem Hinweis "Gestartet und
  // abgebrochen", damit ein Abbruch nicht wie ein eigenständiges Ereignis im
  // Tagebuch auftaucht.
  let rows=[];
  list.forEach(e=>{
    if(e.kind==='game'&&gameStatus(e)==='dropped'){
      rows.push({entry:e,part:'dropped',date:e.startDate||e.date});
    } else if(e.kind==='game'&&gameHasEntryBetween(e)){
      rows.push({entry:e,part:'start',date:e.startDate});
      rows.push({entry:e,part:'end',date:e.finishDate});
    } else {
      rows.push({entry:e,part:'full',date:e.date});
    }
  });
  rows.sort((a,b)=>(b.date||'').localeCompare(a.date||'')||(b.entry.created||0)-(a.entry.created||0));

  // Einträge pro Monat vorab zählen (für die Gruppen-Überschrift)
  const groupCounts={};
  rows.forEach(row=>{
    const d=row.date?new Date(row.date+'T00:00:00'):null;
    const group=d?`${MONTHS[d.getMonth()]} ${d.getFullYear()}`:'Ohne Datum';
    groupCounts[group]=(groupCounts[group]||0)+1;
  });

  let html='', lastGroup='';
  rows.forEach(row=>{
    const e=row.entry, isStart=row.part==='start', isDropped=row.part==='dropped';
    const d=row.date?new Date(row.date+'T00:00:00'):null;
    const group=d?`${MONTHS[d.getMonth()]} ${d.getFullYear()}`:'Ohne Datum';
    if(group!==lastGroup){
      const n=groupCounts[group];
      html+=`<div class="dmonth">${group}<span class="dmonth-count">${n} Eintr${n===1?'ag':'äge'}</span></div>`;
      lastGroup=group;
    }

    const day=d?d.getDate():'–';
    const canPick=(e.kind==='movie'||e.kind==='season');
    const posterInner=e.poster
      ?`<img class="dposter" src="${e.poster}" alt="" loading="lazy">`
      :`<div class="dposter-ph">${e.kind==='season'?'S':e.kind==='game'?'G':'F'}</div>`;
    const poster=canPick
      ?`<div class="dposter-wrap dposter-pick" data-id="${e.id}" title="Cover ändern">${posterInner}<span class="dposter-pick-ic">✎</span></div>`
      :`<div class="dposter-wrap">${posterInner}</div>`;
    const badge=e.kind==='season'
      ?`<span class="dbadge serie">S${String(e.season||1).padStart(2,'0')}</span>`
      :e.kind==='game'
      ?`<span class="dbadge game">Spiel</span>`
      :`<span class="dbadge film">Film</span>`;

    // Leichtgewichtiger "Gestartet"-Marker: zeigt nur Titel + Verweis auf das
    // Enddatum, ohne Bewertung/Kommentar/Nominierung/Löschen (die gehören zum
    // Haupteintrag, der separat am Enddatum erscheint). Abgebrochene Spiele
    // nutzen dieselbe leichtgewichtige Darstellung, aber ohne Verweis auf ein
    // Enddatum - stattdessen der kombinierte Hinweis "Gestartet und abgebrochen".
    if(isStart||isDropped){
      html+=`<div class="dentry-wrap">
        <div class="dentry">
          <div class="dday">${day}</div>
          <div class="dposter-wrap">${posterInner}</div>
          <div class="dinfo">
            <div class="dtitle">${esc(e.title)}<span class="dyear">${esc(e.year||'')}</span></div>
            <div class="dsub">${badge}<span class="stbadge marker${isDropped?' dropped':''}">${isDropped?'Gestartet und abgebrochen':'Gestartet'}</span></div>
            ${isStart?`<div class="dgamedates">Beendet: ${fmtDate(e.finishDate)}</div>`:''}
          </div>
          <div class="drating"></div>
          <div class="dactions">
            <button class="dbtn" data-id="${e.id}" onclick="openFromDiary(this.dataset.id)">Öffnen</button>
          </div>
        </div>
      </div>`;
      return;
    }

    const cmt=e.comment?`<span class="dcomment">„${esc(e.comment)}"</span>`:'';

    // Serien-Status + Nominierungen
    let st='';
    if(e.kind==='season'){
      const s=seriesStatus(e);
      if(s){
        st+=`<select class="stbadge-select ${s}" data-id="${e.id}" title="Status manuell setzen"
              onchange="setStatusOverride(this)" onclick="event.stopPropagation()">
          <option value="started"${s==='started'?' selected':''}>Begonnen</option>
          <option value="continued"${s==='continued'?' selected':''}>Fortgesetzt</option>
          <option value="completed"${s==='completed'?' selected':''}>Abgeschlossen</option>
        </select>`;
      }
      if(e.mini) st+='<span class="stbadge mini">Mini</span>';
    }
    if(e.kind==='game'){
      const gs=gameStatus(e);
      st+=`<select class="stbadge-select ${gs}" data-id="${e.id}" title="Status manuell setzen"
            onchange="setStatusOverride(this)" onclick="event.stopPropagation()">
        <option value="playing" ${gs==='playing'?'selected':''}>Spielt gerade</option>
        <option value="dropped" ${gs==='dropped'?'selected':''}>Abgebrochen</option>
        <option value="completed"${gs==='completed'?'selected':''}>Abgeschlossen</option>
      </select>`;
    }
    const nn=(e.noms||[]).length;
    if(nn) st+=`<span class="dbadge rw" title="${(e.noms||[]).map(c=>(CAT_BY_ID[c]||{}).n||c).join(', ')}"><i class="fa-solid fa-trophy"></i> ${nn}</span>`;
    const rwIcon=e.rewatch?'<span class="dbadge rw" title="Erneut gesehen">↻</span>':'';
    const manualBadge=(e.imported||e.letterboxd)?'':'<span class="dbadge manual" title="Manuell hinzugefügt (nicht importiert)">Manuell</span>';
    // Bei kombinierten (nicht gesplitteten) Spiele-Einträgen mit abweichendem
    // Start- und Enddatum beide Daten sichtbar machen.
    const gameDates=(e.kind==='game'&&e.startDate&&e.startDate!==row.date)
      ?`<div class="dgamedates">Gestartet: ${fmtDate(e.startDate)}</div>`:'';

    html+=`<div class="dentry-wrap">
      <div class="dentry">
        <div class="dday">${day}</div>
        ${poster}
        <div class="dinfo">
          <div class="dtitle">${esc(e.title)}<span class="dyear">${esc(e.year||'')}</span></div>
          <div class="dsub">${badge}${st}${manualBadge}</div>
          ${gameDates}
          ${cmt}
        </div>
        <div class="drating">
          <span class="d-rate" data-id="${e.id}">${starsHTML(e.rating)}</span>
          ${rwIcon}
        </div>
        <div class="dactions">
          <button class="dbtn rwtoggle${e.rewatch?' active':''}" data-id="${e.id}" onclick="toggleRewatch(this.dataset.id)" title="${e.rewatch?'Als Erstsichtung markieren':'Als Rewatch markieren'}">↻</button>
          <button class="dbtn nom${nn?' active':''}" data-id="${e.id}" onclick="toggleNomPanel(this.dataset.id)" title="Nominieren"><i class="fa-solid fa-trophy"></i>${nn?' '+nn:''}</button>
          <button class="dbtn" data-id="${e.id}" onclick="openFromDiary(this.dataset.id)">Öffnen</button>
          <button class="dbtn del" data-id="${e.id}" onclick="delDiary(this.dataset.id)" title="Löschen">✕</button>
        </div>
      </div>
      <div class="dnom-panel" id="nom-${e.id}"></div>
    </div>`;
  });

  const hasMore=diaryYearsShown<years.length;
  if(hasMore){
    html+=`<div class="diary-loadmore" id="diary-loadmore">
      <span class="spin">↻</span> Lade ${years[diaryYearsShown]||'weitere Einträge'}…
    </div>`;
  }

  cont.innerHTML=html;
  bindDiaryStars();
  bindDiaryPosterPick();
  bindDiaryLoadMore(hasMore);
}

// Lädt beim Erreichen des Listenendes automatisch das nächstältere Jahr nach,
// statt alle Einträge auf einmal zu rendern (bei mehreren hundert Einträgen
// spürbar schneller). Zeigt dabei kurz eine Ladeanimation.
function bindDiaryLoadMore(hasMore){
  if(diaryLoadMoreObs){ diaryLoadMoreObs.disconnect(); diaryLoadMoreObs=null; }
  if(!hasMore) return;
  const sentinel=document.getElementById('diary-loadmore');
  if(!sentinel) return;
  diaryLoadMoreObs=new IntersectionObserver(entries=>{
    if(!entries.some(en=>en.isIntersecting)) return;
    diaryLoadMoreObs.disconnect(); diaryLoadMoreObs=null;
    setTimeout(()=>{ diaryYearsShown++; renderDiary(); }, 220);
  }, {root:null, rootMargin:'300px'});
  diaryLoadMoreObs.observe(sentinel);
}

function bindDiaryPosterPick(){
  document.querySelectorAll('.dposter-pick').forEach(el=>{
    el.onclick=()=>{
      const e=diary.find(x=>x.id===el.dataset.id);
      if(!e)return;
      openPosterPicker({
        entryId: e.id,
        key: e.key||'',
        kind: e.kind==='season'?'series':'movie',
        title: e.title, year: e.year,
      });
    };
  });
}

// ── Nominieren direkt aus der Tagebuchliste ────────────────────────────────────
function toggleNomPanel(id){
  const panel=document.getElementById('nom-'+id);
  if(!panel)return;
  if(panel.classList.contains('open')){
    panel.classList.remove('open'); panel.innerHTML=''; return;
  }
  document.querySelectorAll('.dnom-panel.open').forEach(p=>{p.classList.remove('open');p.innerHTML='';});

  const e=diary.find(x=>x.id===id);
  if(!e)return;
  const catsSrc=e.kind==='movie'?CATS_FILM:e.kind==='season'?CATS_SERIES:CATS_GAMES;
  const cats=catsSrc.filter(c=>eligible(e,c.id));

  panel.innerHTML='<div class="noms-chips"></div>';
  renderNomChips(panel.querySelector('.noms-chips'), e, cats, ()=>{
    renderDiary();
    toggleNomPanel(id);   // Panel nach dem Neu-Rendern wieder öffnen
  });
  panel.classList.add('open');
}

// ── Nominierungs-Chips: gemeinsame Logik für Modal und Tagebuch-Panel ─────────
function chipLabel(entry,cat){
  if(cat.person||cat.text){
    const p=(entry.nomPeople||{})[cat.id];
    if(p&&p.name) return `${cat.n}: ${p.name}`;
  }
  return cat.n;
}

function renderNomChips(container,entry,cats,onChange){
  const cur=entry.noms||[];
  container.innerHTML=cats.length
    ? cats.map(c=>`<button class="nchip${cur.includes(c.id)?' on':''}" data-c="${c.id}"
        ${c.hint?`title="${c.hint}"`:''}>${esc(chipLabel(entry,c))}</button>`).join('')
    : '<span class="dcomment">Keine passenden Kategorien verfügbar</span>';

  container.querySelectorAll('.nchip').forEach(b=>{
    b.onclick=()=>{
      const catId=b.dataset.c, cat=CAT_BY_ID[catId];
      if(cat&&(cat.person||cat.text)){
        openActorPicker(entry.id,catId,onChange);
      } else {
        toggleSimpleNom(entry.id,catId,cur,onChange);
      }
    };
  });
}

async function toggleSimpleNom(entryId,catId,curList,onChange){
  const next=curList.includes(catId)?curList.filter(x=>x!==catId):[...curList,catId];
  try{
    const d=await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:entryId,noms:next})
    }).then(r=>r.json());
    if(d.ok){
      diary=d.entries;
      showToast(curList.includes(catId)?'Nominierung entfernt':'Nominiert');
      onChange&&onChange();
    } else showToast('⚠ '+d.error,true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

// ── Schauspieler-Auswahlmodal (Fotos via TMDB, Textfallback) ──────────────────
let actorPickerEntryId=null, actorPickerCat=null, actorPickerRefresh=null;

function initials(name){
  return (name||'').split(' ').filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('');
}

async function openActorPicker(entryId,catId,refreshFn){
  actorPickerEntryId=entryId; actorPickerCat=catId; actorPickerRefresh=refreshFn;
  const e=diary.find(x=>x.id===entryId);
  const cat=CAT_BY_ID[catId];
  document.getElementById('actorModalTitle').textContent=cat?cat.n:'Person auswählen';
  document.getElementById('actorModalSub').textContent=e
    ? e.title+(e.kind==='season'?` · Staffel ${e.season||1}`:'')
    : '';
  const body=document.getElementById('actorModalBody');
  body.innerHTML='<div class="ep-loading">Lade Besetzung…</div>';
  document.getElementById('actorModal').classList.add('open');
  document.body.style.overflow='hidden';

  if(!e){ body.innerHTML='<div class="dempty">Eintrag nicht gefunden.</div>'; return; }

  let cast=[];
  if(!cat||!cat.text){
    try{
      const kind=e.kind==='season'?'series':'movie';
      const q=`title=${encodeURIComponent(e.title)}&year=${encodeURIComponent((e.year||'').toString())}&kind=${kind}`;
      const d=await fetch('/api/cast?'+q).then(r=>r.json());
      cast=d.cast||[];
    }catch{ cast=[]; }
  }

  renderActorPickerBody(cast);
}

function renderActorPickerBody(cast){
  const body=document.getElementById('actorModalBody');
  const e=diary.find(x=>x.id===actorPickerEntryId);
  const curPerson=e&&e.nomPeople?e.nomPeople[actorPickerCat]:null;

  let html='';
  if(cast.length){
    html+=`<div class="cast-grid">${cast.map(p=>{
      const sel=!!(curPerson&&p.id!=null&&curPerson.tmdbId===p.id);
      const photo=p.photo
        ?`<img class="cast-photo" src="${p.photo}" alt="">`
        :`<div class="cast-photo-ph">${esc(initials(p.name))}</div>`;
      return `<div class="cast-item${sel?' sel':''}" data-id="${p.id}" data-name="${esc(p.name)}" data-photo="${p.photo||''}">
        ${photo}
        <div class="cast-name">${esc(p.name)}</div>
        ${p.character?`<div class="cast-role">${esc(p.character)}</div>`:''}
      </div>`;
    }).join('')}</div>`;
  } else {
    const cat=CAT_BY_ID[actorPickerCat];
    html+=`<div class="dempty">${cat&&cat.text?'Wert eintragen:':'Keine Besetzung gefunden. Name manuell eingeben:'}</div>`;
  }

  const manualVal=(curPerson&&!cast.some(p=>p.id===curPerson.tmdbId))?curPerson.name:'';
  html+=`<div class="cast-manual">
    <input type="text" id="castManualName" placeholder="Wert eingeben…" value="${esc(manualVal)}">
    <button class="rate-save" id="castManualSave">Bestätigen</button>
  </div>`;

  if(curPerson){
    html+='<div class="cast-remove"><button class="rate-clear" id="castRemove">Nominierung entfernen</button></div>';
  }

  body.innerHTML=html;

  body.querySelectorAll('.cast-item').forEach(item=>{
    item.onclick=()=>pickActor({
      tmdbId:+item.dataset.id, name:item.dataset.name, photo:item.dataset.photo||null
    });
  });
  const manualBtn=body.querySelector('#castManualSave');
  if(manualBtn) manualBtn.onclick=()=>{
    const name=body.querySelector('#castManualName').value.trim();
    if(!name){ showToast('⚠ Bitte einen Namen eingeben',true); return; }
    pickActor({tmdbId:null,name,photo:null});
  };
  const rmBtn=body.querySelector('#castRemove');
  if(rmBtn) rmBtn.onclick=()=>removeActorNom();
}

async function pickActor(person){
  const e=diary.find(x=>x.id===actorPickerEntryId);
  if(!e)return;
  const noms=[...(e.noms||[])];
  if(!noms.includes(actorPickerCat)) noms.push(actorPickerCat);
  const nomPeople={...(e.nomPeople||{})};
  nomPeople[actorPickerCat]=person;
  try{
    const d=await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:e.id,noms,nomPeople})
    }).then(r=>r.json());
    if(d.ok){
      diary=d.entries;
      showToast('Nominiert: '+person.name);
      closeActorPicker();
      actorPickerRefresh&&actorPickerRefresh();
    } else showToast('⚠ '+d.error,true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

async function removeActorNom(){
  const e=diary.find(x=>x.id===actorPickerEntryId);
  if(!e)return;
  const noms=(e.noms||[]).filter(x=>x!==actorPickerCat);
  const nomPeople={...(e.nomPeople||{})};
  delete nomPeople[actorPickerCat];
  try{
    const d=await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:e.id,noms,nomPeople})
    }).then(r=>r.json());
    if(d.ok){
      diary=d.entries;
      showToast('Nominierung entfernt');
      closeActorPicker();
      actorPickerRefresh&&actorPickerRefresh();
    } else showToast('⚠ '+d.error,true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

function closeActorPicker(){
  document.getElementById('actorModal').classList.remove('open');
  const mOpen=document.getElementById('mo').classList.contains('open');
  const iOpen=document.getElementById('impModal').classList.contains('open');
  document.body.style.overflow=(mOpen||iOpen)?'hidden':'';
}

// Sterne direkt in der Tagebuchliste bearbeitbar machen (ohne Modal zu öffnen)
function bindDiaryStars(){
  document.querySelectorAll('.d-rate').forEach(wrap=>{
    const id=wrap.dataset.id;
    bindStars(wrap.querySelector('.stars'),async v=>{
      wrap.innerHTML=starsHTML(v);
      const d=await fetch('/api/diary',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id,rating:v})
      }).then(r=>r.json());
      if(d.ok){ diary=d.entries; showToast('Bewertung gespeichert'); bindDiaryStars(); }
      else showToast('⚠ '+d.error,true);
    });
  });
}

function openFromDiary(id){
  const e=diary.find(x=>x.id===id);
  if(!e)return;
  const isMov=e.kind==='movie', isGame=e.kind==='game';

  if(isGame){
    // Games haben keinen eigenen Tab/Grid – Item aus allGames suchen,
    // sonst aus den Tagebuch-Daten selbst rekonstruieren.
    let item=allGames.find(x=>x.key===e.key);
    if(!item){
      item={t:e.title, y:e.year, key:e.key, poster:e.poster, genre:e.genre,
            platforms:e.platform?[e.platform]:[]};
    }
    openModalForItem(item,'game',true);
    return;
  }

  const src=isMov?allMovies:allSeries;
  const item=src.find(x=>x.key===e.key);
  if(item){
    switchTab(isMov?'movies':'series');
    document.getElementById('search').value='';
    document.getElementById('filterGenre').value='';
    render();
    const idx=getList().findIndex(x=>x.key===e.key);
    if(idx>=0){ openModal(idx,true); if(!isMov) modalSeason=e.season||1; }
    return;
  }
  // Kein lokaler Bibliotheks-Treffer (z.B. per Letterboxd/CSV importiert, aber
  // nicht in der eigenen Sammlung vorhanden) - Modal trotzdem mit den im
  // Tagebuch gespeicherten Daten öffnen, damit Bewertung/Notiz bearbeitbar bleiben.
  const stub={t:e.title, y:e.year, key:e.key, poster:e.poster, genre:e.genre, _noLibrary:true};
  if(!isMov) stub._seasons=[e.season||1];
  openModalForItem(stub, isMov?'movie':'series', true);
  if(!isMov){ modalSeason=e.season||1; renderRateBox(); }
}

async function delDiary(id){
  const snapshot=diary.find(x=>x.id===id);
  try{
    const d=await fetch('/api/diary?id='+encodeURIComponent(id),{method:'DELETE'}).then(r=>r.json());
    if(d.ok){
      diary=d.entries; renderDiary();
      showToast('Eintrag gelöscht', false, snapshot?()=>undoDeleteDiary(snapshot):null);
    }
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

async function undoDeleteDiary(snapshot){
  const payload={...snapshot};
  delete payload.id;
  delete payload.created;
  try{
    const d=await fetch('/api/diary',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    }).then(r=>r.json());
    if(d.ok){
      diary=d.entries;
      refreshVisibleView();
      showToast('Eintrag wiederhergestellt');
    } else showToast('⚠ '+(d.error||'Wiederherstellen fehlgeschlagen'),true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

// ── Letterboxd-Sync ─────────────────────────────────────────────────────────
function fmtSyncTime(ts){
  if(!ts) return 'Noch nie synchronisiert';
  const d=new Date(ts*1000);
  return 'Zuletzt: '+d.toLocaleDateString('de-DE')+' '+d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
}

async function openLbModal(){
  document.getElementById('lbResult').innerHTML='';
  document.getElementById('lbModal').classList.add('open');
  document.body.style.overflow='hidden';
  try{
    const cfg=await fetch('/api/letterboxd/config').then(r=>r.json());
    document.getElementById('lbUsername').value=cfg.username||'';
    document.getElementById('lbLastSync').textContent=fmtSyncTime(cfg.lastSync);
  }catch{ document.getElementById('lbLastSync').textContent='Konnte Einstellungen nicht laden'; }
}

function closeLbModal(){
  document.getElementById('lbModal').classList.remove('open');
  document.body.style.overflow='';
}

async function saveLbUsername(){
  const name=document.getElementById('lbUsername').value.trim();
  const res=document.getElementById('lbResult');
  try{
    const d=await fetch('/api/letterboxd/config',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:name})
    }).then(r=>r.json());
    if(d.ok){ showToast('Benutzername gespeichert'); }
    else res.innerHTML=`<div class="imp-res" style="color:#e74c3c">⚠ ${esc(d.error||'Fehler')}</div>`;
  }catch{ res.innerHTML='<div class="imp-res" style="color:#e74c3c">⚠ Netzwerkfehler</div>'; }
}

async function runLbSync(){
  const res=document.getElementById('lbResult');
  const name=document.getElementById('lbUsername').value.trim();
  if(!name){ showToast('⚠ Bitte zuerst einen Benutzernamen eintragen',true); return; }
  await saveLbUsername();
  res.innerHTML='<div class="imp-res">Synchronisiere…</div>';
  try{
    const d=await fetch('/api/letterboxd/sync').then(r=>r.json());
    if(!d.ok){
      res.innerHTML=`<div class="imp-res" style="color:#e74c3c">⚠ ${esc(d.error)}</div>`;
      return;
    }
    diary=d.entries;
    res.innerHTML=`<div class="imp-res">
      ✓ <b>${d.added}</b> neue Einträge<br>
      ${d.skipped?`↩ <b>${d.skipped}</b> bereits vorhanden<br>`:''}
      <b>${d.matched}</b> mit deiner Bibliothek verknüpft
    </div>`;
    document.getElementById('lbLastSync').textContent='Gerade eben synchronisiert';
    buildYearSelect();
    if(d.added) refreshVisibleView();
    showToast(d.added?`${d.added} neue Letterboxd-Einträge`:'Bereits aktuell');
    if(d.added) backfillCoversLoop();
  }catch{
    res.innerHTML='<div class="imp-res" style="color:#e74c3c">⚠ Sync fehlgeschlagen</div>';
  }
}

// Bei Seitenaufruf einmal still im Hintergrund synchronisieren, falls ein
// Benutzername hinterlegt ist (zusätzlich zum Auto-Sync bei Server-Start).
async function silentLetterboxdSync(){
  try{
    const cfg=await fetch('/api/letterboxd/config').then(r=>r.json());
    if(!cfg.username) return;
    const d=await fetch('/api/letterboxd/sync').then(r=>r.json());
    if(d.ok&&d.added>0){
      diary=d.entries;
      showToast(`${d.added} neue Letterboxd-Einträge synchronisiert`);
      buildYearSelect();
      refreshVisibleView();
      backfillCoversLoop();
    }
  }catch{ /* offline oder kein Netzwerk - stumm ignorieren */ }
}

// ── Import/Export-Menü (Dropdown) ───────────────────────────────────────────
function toggleIeMenu(e){
  if(e) e.stopPropagation();
  document.getElementById('ieMenuPop').classList.toggle('open');
}
function closeIeMenu(){
  document.getElementById('ieMenuPop').classList.remove('open');
}
document.addEventListener('click',e=>{
  const wrap=document.getElementById('ieMenuWrap');
  if(wrap && !wrap.contains(e.target)) closeIeMenu();
});

// ── Import ────────────────────────────────────────────────────────────────────
function openImport(){
  document.getElementById('impResult').innerHTML='';
  document.getElementById('impPasteText').value='';
  document.getElementById('impModal').classList.add('open');
  document.body.style.overflow='hidden';
}
function closeImport(){
  document.getElementById('impModal').classList.remove('open');
  document.body.style.overflow='';
}

function setupImport(){
  const drop=document.getElementById('impDrop');
  const file=document.getElementById('impFile');
  drop.onclick=()=>file.click();
  file.onchange=()=>{ if(file.files[0]) readImport(file.files[0]); file.value=''; };
  ['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop',e=>{
    const f=e.dataTransfer.files[0];
    if(f) readImport(f);
  });
}

function readImport(f){
  const res=document.getElementById('impResult');
  res.innerHTML='<div class="imp-res">Lese Datei…</div>';
  const rd=new FileReader();
  rd.onload=()=>runImportText(rd.result);
  rd.onerror=()=>{ res.innerHTML='<div class="imp-res" style="color:#e74c3c">⚠ Datei nicht lesbar</div>'; };
  rd.readAsText(f,'utf-8');
}

function runImportPaste(){
  const txt=document.getElementById('impPasteText').value;
  if(!txt.trim()){
    showToast('⚠ Bitte erst Text einfügen',true);
    return;
  }
  runImportText(txt);
}

async function runImportText(text){
  const res=document.getElementById('impResult');
  res.innerHTML='<div class="imp-res">Importiere…</div>';
  try{
    const d=await fetch('/api/import',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:text})
    }).then(r=>r.json());
    if(!d.ok){
      res.innerHTML=`<div class="imp-res" style="color:#e74c3c">⚠ ${esc(d.error)}</div>`;
      return;
    }
    diary=d.entries;
    res.innerHTML=`<div class="imp-res">
      ✓ <b>${d.added}</b> Einträge importiert<br>
      ${d.skipped?`↩ <b>${d.skipped}</b> Dubletten übersprungen<br>`:''}
      <b>${d.matched}</b> mit deiner Bibliothek verknüpft (Poster &amp; Genre)
    </div>`;
    refreshVisibleView(); buildYearSelect();
    showToast(`${d.added} Einträge importiert`);
    if(d.added) backfillCoversLoop();
  }catch(err){
    res.innerHTML='<div class="imp-res" style="color:#e74c3c">⚠ Import fehlgeschlagen</div>';
  }
}

// ── Games: hinzufügen (IGDB-Suche) ────────────────────────────────────────────
function openGameAdd(){
  document.getElementById('gameSearchInput').value='';
  document.getElementById('gameSearchResults').innerHTML='';
  document.getElementById('gameAddModal').classList.add('open');
  document.body.style.overflow='hidden';
  document.getElementById('gameSearchInput').focus();
}
function closeGameAdd(){
  document.getElementById('gameAddModal').classList.remove('open');
  document.body.style.overflow='';
}

function gameManualHtml(q){
  return `<div class="cast-manual">
    <input type="text" id="gameManualTitle" placeholder="Titel manuell…" value="${esc(q)}">
    <button class="rate-save" id="gameManualSave">Ohne IGDB hinzufügen</button>
  </div>`;
}
function bindGameManual(){
  const btn=document.getElementById('gameManualSave');
  if(btn) btn.onclick=()=>{
    const t=document.getElementById('gameManualTitle').value.trim();
    if(!t){ showToast('⚠ Bitte einen Titel eingeben',true); return; }
    addGame({title:t});
  };
}

async function runGameSearch(){
  const q=document.getElementById('gameSearchInput').value.trim();
  const res=document.getElementById('gameSearchResults');
  if(!q) return;
  res.innerHTML='<div class="ep-loading">Suche…</div>';
  try{
    const d=await fetch('/api/games/search?title='+encodeURIComponent(q)).then(r=>r.json());
    const results=d.results||[];
    if(!results.length){
      res.innerHTML='<div class="dempty">Keine Treffer gefunden.</div>'+gameManualHtml(q);
      bindGameManual(); return;
    }
    res.innerHTML=`<div class="cast-grid">${results.map((g,i)=>`
      <div class="cast-item" data-i="${i}">
        ${g.cover?`<img class="cast-photo" src="${g.cover}" alt="">`:'<div class="cast-photo-ph">G</div>'}
        <div class="cast-name">${esc(g.title)}</div>
        <div class="cast-role">${esc(g.year||'')}${g.platforms&&g.platforms.length?' · '+esc(g.platforms.slice(0,2).join(', ')):''}</div>
      </div>`).join('')}</div>` + gameManualHtml(q);
    res.querySelectorAll('.cast-item').forEach(el=>{
      el.onclick=()=>addGame(results[+el.dataset.i]);
    });
    bindGameManual();
  }catch{
    res.innerHTML='<div class="dempty">⚠ Suche fehlgeschlagen (keine Verbindung zu IGDB?)</div>'+gameManualHtml(q);
    bindGameManual();
  }
}

async function addGame(g){
  try{
    const d=await fetch('/api/games/add',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        title: g.title,
        igdb_id: g.igdb_id||null,
        platform: (g.platforms&&g.platforms[0])||''
      })
    }).then(r=>r.json());
    if(d.ok){
      let entry=allGames.find(x=>x.key===d.game.key);
      if(!entry){
        entry={
          t:d.game.title, y:d.game.year, key:d.game.key, poster:d.game.cover,
          genre:d.game.genre, platforms:d.game.platforms, not_found:d.game.not_found
        };
        allGames.push(entry);
      }
      closeGameAdd();
      // Direkt ins Bewertungs-Modal springen – Games haben keinen eigenen Tab,
      // Sinn des Hinzufügens ist ein Tagebuch-Eintrag.
      openModalForItem(entry,'game');
      showToast('Spiel gefunden: '+d.game.title+' – jetzt eintragen');
    } else showToast('⚠ '+d.error,true);
  }catch{ showToast('⚠ Netzwerkfehler',true); }
}

function askDelGame(m){
  if(!confirm(`"${m.t}" wirklich aus der Bibliothek entfernen? Zugehörige Tagebuch-Einträge werden ebenfalls gelöscht.`)) return;
  fetch('/api/games?key='+encodeURIComponent(m.key),{method:'DELETE'})
    .then(r=>r.json()).then(d=>{
      if(d.ok){
        allGames=allGames.filter(x=>x.key!==m.key);
        diary=d.entries;
        closeModal();
        if(currentTab==='diary') renderDiary();
        else if(currentTab==='stats') renderStats();
        showToast('Spiel entfernt');
      } else showToast('⚠ '+(d.error||'Fehler'),true);
    }).catch(()=>showToast('⚠ Netzwerkfehler',true));
}

// ── Games: Backloggd-Journal-Import ───────────────────────────────────────────
function openGameImport(){
  document.getElementById('gameImportText').value='';
  document.getElementById('gameImportResult').innerHTML='';
  document.getElementById('gameImportModal').classList.add('open');
  document.body.style.overflow='hidden';
}
function closeGameImport(){
  document.getElementById('gameImportModal').classList.remove('open');
  document.body.style.overflow='';
}

// ── Games: Offene Spiele (Sammel-Ansicht "Spielt gerade") ─────────────────────
function openGamesList(){
  return diary.filter(e=>e.kind==='game'&&gameStatus(e)==='playing')
    .sort((a,b)=>(b.startDate||b.date||'').localeCompare(a.startDate||a.date||''));
}
function updateOpenGamesCount(){
  const el=document.getElementById('ogBtnCount');
  if(!el) return;
  const n=openGamesList().length;
  el.textContent=n?' '+n:'';
  el.style.display=n?'':'none';
}
let ogPicked={};
function openOpenGames(){
  ogPicked={};
  document.getElementById('openGamesModal').classList.add('open');
  document.body.style.overflow='hidden';
  renderOpenGames();
}
function closeOpenGames(){
  document.getElementById('openGamesModal').classList.remove('open');
  document.body.style.overflow='';
}
function renderOpenGames(){
  const list=document.getElementById('openGamesList');
  const open=openGamesList();
  if(!open.length){
    list.innerHTML='<div class="dempty" style="padding:20px 0">Keine offenen Spiele – alles abgeschlossen oder abgebrochen.</div>';
    return;
  }
  const today=new Date().toISOString().slice(0,10);
  list.innerHTML=open.map(e=>`
    <div class="og-row" data-id="${e.id}">
      <div class="og-info">
        <div class="og-title">${esc(e.title)}<span class="dyear">${esc(e.year||'')}</span></div>
        <div class="og-sub">${e.platform?esc(e.platform)+' · ':''}Start: ${e.startDate?fmtDate(e.startDate):'unbekannt'}</div>
      </div>
      <div class="og-finish">
        <div class="og-date-wrap">
          <span class="og-date-lbl">Datum (Ende/Abbruch)</span>
          <input type="date" id="og-date-${e.id}" value="${e.startDate||today}" title="Datum (Ende/Abbruch) - vorausgefüllt mit dem Startdatum">
        </div>
        <span id="og-stars-${e.id}">${starsHTML(e.rating||0)}</span>
        <div class="og-actions">
          <button class="btn btn-ghost" onclick="ogSetStatus('${e.id}','dropped')">Abgebrochen</button>
          <button class="btn" onclick="ogSetStatus('${e.id}','completed')">Abgeschlossen</button>
        </div>
      </div>
    </div>`).join('');
  open.forEach(e=>{
    ogPicked[e.id]=e.rating||0;
    attachOgStars(e.id);
  });
}
function attachOgStars(id){
  const starEl=document.getElementById('og-stars-'+id);
  if(!starEl) return;
  bindStars(starEl.querySelector('.stars'),v=>{
    ogPicked[id]=v;
    starEl.innerHTML=starsHTML(v);
    attachOgStars(id);
  });
}
async function ogSetStatus(id,status){
  const dateEl=document.getElementById('og-date-'+id);
  const dateVal=(dateEl&&dateEl.value)||new Date().toISOString().slice(0,10);
  const rating=ogPicked[id]||null;
  if(status==='dropped'){
    // Abgebrochene Spiele bekommen keinen eigenen Tagebuch-Eintrag am
    // Abbruchdatum - nur der Status wechselt, das ursprüngliche 'date' (=
    // Startdatum) bleibt unverändert, damit der Eintrag weiterhin an seinem
    // Start einsortiert bleibt statt als neue Zeile am Abbruchtag.
    await saveDiaryEntry({id, statusOverride:status, finishDate:dateVal, rating});
  } else {
    await saveDiaryEntry({id, statusOverride:status, finishDate:dateVal, date:dateVal, rating});
  }
  showToast(status==='dropped'?'Als abgebrochen markiert':'Als abgeschlossen markiert');
  renderOpenGames(); renderDiary();
}

async function runGameImport(){
  const text=document.getElementById('gameImportText').value;
  const res=document.getElementById('gameImportResult');
  if(!text.trim()){ showToast('⚠ Bitte zuerst Text einfügen',true); return; }
  res.innerHTML='<div class="imp-res">Importiere… (kann bei vielen Titeln etwas dauern)</div>';
  try{
    const d=await fetch('/api/games/import',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})
    }).then(r=>r.json());
    if(!d.ok){
      res.innerHTML=`<div class="imp-res" style="color:#e74c3c">⚠ ${esc(d.error)}</div>`;
      return;
    }
    res.innerHTML=`<div class="imp-res">
      ✓ <b>${d.diary_added}</b> Spielsitzungen importiert<br>
      ${d.diary_skipped_duplicate?`↩ <b>${d.diary_skipped_duplicate}</b> bereits vorhanden<br>`:''}
      <b>${d.games_touched}</b> Spiele in der Bibliothek
    </div>`;
    const gd=await fetch('/api/data').then(r=>r.json());
    allGames=gd.games||[];
    await loadDiary();
    buildYearSelect();
    if(d.diary_added) refreshVisibleView();
    showToast(`${d.diary_added} Spielsitzungen importiert`);
    if(d.diary_added) backfillCoversLoop();
  }catch{
    res.innerHTML='<div class="imp-res" style="color:#e74c3c">⚠ Import fehlgeschlagen</div>';
  }
}

// ── Jahresauswertung ──────────────────────────────────────────────────────────
function diaryYears(){
  const ys=[...new Set(diary.map(e=>(e.date||'').slice(0,4)).filter(Boolean))];
  return ys.sort((a,b)=>b.localeCompare(a));
}

function buildYearSelect(){
  const sel=document.getElementById('statsYear');
  const years=diaryYears();
  const cur=new Date().getFullYear().toString();
  if(!years.includes(cur)) years.unshift(cur);
  const prev=sel.value;
  sel.innerHTML=`<option value="all">Gesamt</option><option value="top10">★ Meine Top 10</option>`+years.map(y=>`<option value="${y}">${y}</option>`).join('');
  sel.value=(prev==='all'||years.includes(prev))?prev:years[0];   // Standard: aktuelles Jahr
}

function renderStats(){
  const year=document.getElementById('statsYear').value;
  if(year==='all'){ renderStatsAll(); return; }
  if(year==='top10'){
    document.getElementById('stats-body').innerHTML=`<div class="stats-wide stats-3col">
      <div class="swide-card" id="top10-movie"></div>
      <div class="swide-card" id="top10-season"></div>
      <div class="swide-card" id="top10-game"></div></div>`;
    renderTop10(); return;
  }
  const ent=diary.filter(e=>(e.date||'').startsWith(year));
  const body=document.getElementById('stats-body');

  if(!ent.length){
    body.innerHTML=`<div class="dempty">Keine Einträge für ${year}.</div>`;
    return;
  }

  const films   = ent.filter(e=>e.kind==='movie');
  const seasons = ent.filter(e=>e.kind==='season');
  const games   = ent.filter(e=>e.kind==='game');
  const rated   = ent.filter(e=>e.rating);
  const avg     = rated.length?(rated.reduce((s,e)=>s+e.rating,0)/rated.length):0;
  const rewatch = ent.filter(e=>e.rewatch).length;

  // Monatsverteilung
  const perMonth=Array(12).fill(0);
  ent.forEach(e=>{ const m=+(e.date||'').slice(5,7); if(m>=1&&m<=12) perMonth[m-1]++; });
  const maxM=Math.max(...perMonth,1);

  // Genres
  const gcount={};
  ent.forEach(e=>(e.genre||'').split(',').map(g=>g.trim()).filter(Boolean)
    .forEach(g=>gcount[g]=(gcount[g]||0)+1));
  const topGenres=Object.entries(gcount).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const maxG=topGenres.length?topGenres[0][1]:1;

  // Bewertungsverteilung (0.5er Schritte)
  const dist={}; for(let v=0.5;v<=5;v+=0.5) dist[v]=0;
  rated.forEach(e=>{ if(dist[e.rating]!==undefined) dist[e.rating]++; });
  const maxD=Math.max(...Object.values(dist),1);

  // Top bewertet (Rewatches zählen nicht für die Platzierung)
  const top=rated.filter(e=>!e.rewatch)
    .sort((a,b)=>b.rating-a.rating||(b.date||'').localeCompare(a.date||''))
    .slice(0,8);

  // Bester Monat
  const bestIdx=perMonth.indexOf(Math.max(...perMonth));

  body.innerHTML=`
    <div class="stats-grid">
      <div class="scard">
        <div class="scard-lbl">Tagebucheinträge</div>
        <div class="sbig">${ent.length}<span class="sunit">Einträge</span></div>
        <div class="ssplit">
          <div><b>${films.length}</b>Filme</div>
          <div><b>${seasons.length}</b>Staffeln</div>
          <div><b>${games.length}</b>Spiele</div>
        </div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Ø Bewertung</div>
        <div class="sbig">${avg?(avg*2).toFixed(2):'–'}<span class="sunit">/ 10</span></div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Aktivster Monat</div>
        <div class="sbig" style="font-size:24px">${MONTHS[bestIdx]}</div>
        <div class="ssplit"><div><b>${perMonth[bestIdx]}</b>Einträge</div></div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Erneut gesehen</div>
        <div class="sbig">${rewatch}</div>
        <div class="ssplit"><div><b>${ent.length?Math.round(rewatch/ent.length*100):0}%</b>aller Einträge</div></div>
      </div>
    </div>

    <div class="stats-charts">
      <div class="swide-card">
        <div class="scard-lbl">Verteilung über das Jahr</div>
        <div class="months">
          ${perMonth.map((n,i)=>`
            <div class="mcell">
              <span class="mval">${n||''}</span>
              <div class="mbar-wrap"><div class="mbar" style="height:${n/maxM*100}%"></div></div>
              <span class="mlbl">${MSHORT[i]}</span>
            </div>`).join('')}
        </div>
      </div>

      ${topGenres.length?`
      <div class="swide-card">
        <div class="scard-lbl">Lieblingsgenres</div>
        ${topGenres.map(([g,n])=>`
          <div class="genrow">
            <span class="genname">${esc(genreLabel(g))}</span>
            <div class="genbar-bg"><div class="genbar" style="width:${n/maxG*100}%"></div></div>
            <span class="genval">${n}</span>
          </div>`).join('')}
      </div>`:''}

      ${rated.length?`
      <div class="swide-card">
        <div class="scard-lbl">Bewertungsverteilung</div>
        <div class="rdist">
          ${Object.entries(dist).map(([v,n])=>`
            <div class="rcol">
              <span class="mval">${n||''}</span>
              <div class="rbar" style="height:${n/maxD*72}px"></div>
              <span class="rlbl">${fmt10(+v)}</span>
            </div>`).join('')}
        </div>
      </div>`:''}

      ${top.length?`
      <div class="swide-card">
        <div class="scard-lbl">Höchste Bewertungen</div>
        <div class="toplist">
          ${top.map((e,i)=>`
            <div class="toprow">
              <span class="toprank">${i+1}</span>
              <span class="topname">${esc(e.title)}${e.kind==='season'
                ?` <span style="color:var(--muted);font-size:11px">S${String(e.season||1).padStart(2,'0')}</span>`:''}</span>
              <span class="r10">${fmt10(e.rating)}</span>
            </div>`).join('')}
        </div>
      </div>`:''}
    </div>

    <div class="stats-wide">
      ${awardsHTML(year,ent,'film')}
      ${awardsHTML(year,ent,'series')}
      ${awardsHTML(year,ent,'game')}
    </div>`;
  bindAwards(year);
}

// ── Top 10 (selbst zusammengestellt, per Drag & Drop sortierbar) ──────────────
let topLists={}, top10Drag=null, top10Timer=null;
const T10_TITLE={movie:'Top 10 Filme',season:'Top 10 Serien',game:'Top 10 Spiele'};

// Listeneinträge: String = Tagebuch-ID, Objekt = externer Treffer (TMDB/IGDB) ohne Tagebucheintrag
function top10Items(kind){
  return (topLists[kind]||[]).map(raw=>{
    if(typeof raw==='string'){ const e=diary.find(x=>x.id===raw); return e?{raw,e}:null; }
    return raw&&raw.title?{raw,e:{...raw,id:null}}:null;
  }).filter(Boolean);
}

async function saveTop10(kind,raws){
  topLists[kind]=raws.slice(0,10);
  renderTop10Kind(kind);
  try{
    const d=await fetch('/api/top',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({kind,ids:topLists[kind]})}).then(r=>r.json());
    if(d.ok&&d.top) topLists=d.top;     // Server hat externe Cover lokal abgelegt
    renderTop10Kind(kind);
  }catch{ showToast('⚠ Top 10 konnte nicht gespeichert werden',true); }
}

function top10Label(e){
  return e.title+(e.kind==='season'&&!e.ext?` S${String(e.season||1).padStart(2,'0')}`:'');
}

function top10Updated(kind){
  const iso=(topLists._updated||{})[kind];
  if(!iso) return '';
  const d=new Date(iso);
  if(isNaN(d)) return '';
  const pad=n=>String(n).padStart(2,'0');
  return `<span class="t10-upd">Zuletzt bearbeitet: ${pad(d.getDate())}.${pad(d.getMonth()+1)}.${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}</span>`;
}

function renderTop10(){ ['movie','season','game'].forEach(renderTop10Kind); }

function renderTop10Kind(kind){
  const box=document.getElementById('top10-'+kind);
  if(!box) return;
  const items=top10Items(kind);
  const raws=()=>items.map(i=>i.raw);

  const slots=Array.from({length:10},(_,i)=>{
    const it=items[i], e=it&&it.e;
    if(!e) return `<div class="t10-slot empty" data-i="${i}"><span class="t10-rank">${i+1}</span><span class="t10-plus">+</span></div>`;
    const img=e.poster?`<img src="${e.poster}" alt="" draggable="false">`
      :`<div class="t10-noimg">${esc(e.title)}</div>`;
    return `<div class="t10-slot" draggable="true" data-i="${i}" title="${esc(top10Label(e))}">
      <span class="t10-rank">${i+1}</span>${img}
      <button class="t10-x" data-i="${i}" title="Entfernen">✕</button>
      <div class="t10-name">${esc(top10Label(e))}</div>
    </div>`;
  }).join('');

  box.innerHTML=`
    <div class="t10-head">
      <div class="scard-lbl" style="margin:0">${T10_TITLE[kind]}</div>
      <div class="t10-search">
        <input type="text" id="t10-q-${kind}" placeholder="Titel suchen und hinzufügen…" autocomplete="off" ${items.length>=10?'disabled':''}>
        <div class="t10-results" id="t10-res-${kind}" hidden></div>
      </div>
      ${items.length?'':`<button class="btn btn-ghost" id="t10-fill-${kind}">Aus besten Wertungen füllen</button>`}
    </div>
    <div class="t10-grid">${slots}</div>
    <div class="t10-foot"><span class="t10-hint">${items.length>1?'Zum Sortieren Poster ziehen und auf einen anderen Platz fallen lassen.':'Suche oben einen Titel, um ihn hinzuzufügen.'}</span>
      ${top10Updated(kind)}</div>`;

  // Entfernen
  box.querySelectorAll('.t10-x').forEach(b=>b.onclick=ev=>{
    ev.stopPropagation();
    const r=raws(); r.splice(+b.dataset.i,1);
    saveTop10(kind,r);
  });
  // Drag & Drop
  box.querySelectorAll('.t10-slot').forEach(s=>{
    const i=+s.dataset.i;
    if(s.draggable) s.ondragstart=ev=>{
      top10Drag=i; s.classList.add('dragging');
      ev.dataTransfer.effectAllowed='move'; ev.dataTransfer.setData('text/plain',String(i));
    };
    s.ondragend=()=>{ top10Drag=null; box.querySelectorAll('.t10-slot').forEach(x=>x.classList.remove('dragging','over')); };
    s.ondragover=ev=>{ if(top10Drag===null) return; ev.preventDefault(); s.classList.add('over'); };
    s.ondragleave=()=>s.classList.remove('over');
    s.ondrop=ev=>{
      ev.preventDefault();
      if(top10Drag===null) return;
      const r=raws();
      const to=Math.min(i,r.length-1);
      const [moved]=r.splice(top10Drag,1);
      r.splice(to,0,moved);
      top10Drag=null;
      saveTop10(kind,r);
    };
  });
  // Autovorschlag
  const fill=document.getElementById('t10-fill-'+kind);
  if(fill) fill.onclick=()=>saveTop10(kind,top10Suggest(kind).slice(0,10).map(e=>e.id));
  // Suche über die Online-Datenbanken (TMDB / IGDB)
  const q=document.getElementById('t10-q-'+kind), res=document.getElementById('t10-res-'+kind);
  if(!q) return;
  const hitHTML=(img,name,year,right,attrs)=>`<div class="t10-hit" ${attrs}>
      ${img?`<img src="${img}" alt="">`:'<span class="t10-hit-ph"></span>'}
      <span class="t10-hit-name">${esc(name)}<small>${esc(year||'')}</small></span>${right||''}</div>`;
  let token=0;
  const run=()=>{
    const my=++token;
    clearTimeout(top10Timer);
    const raw=q.value.trim();
    const term=raw.toLowerCase().replace(/[.\-_:]+/g,' ');
    if(!term){ res.hidden=true; return; }
    const takenExt=new Set(items.map(i=>(i.e.title+'|'+i.e.year).toLowerCase()));
    // Immer Online-Suche (entprellt)
    res.innerHTML='<div class="t10-none">Suche…</div>'; res.hidden=false;
    top10Timer=setTimeout(async()=>{
      let cands=[];
      try{
        if(kind==='game'){
          const d=await fetch('/api/games/search?title='+encodeURIComponent(raw)).then(r=>r.json());
          cands=(d.results||[]).map(r=>({title:r.title,year:r.year,poster:r.cover}));
        }else{
          const d=await fetch('/api/title-search?kind='+(kind==='season'?'series':'movie')+'&q='+encodeURIComponent(raw)).then(r=>r.json());
          cands=(d.candidates||[]).map(r=>({title:r.title,year:r.year,poster:r.poster}));
        }
      }catch{}
      if(my!==token) return;     // neuere Eingabe hat Vorrang
      cands=cands.filter(c=>!takenExt.has((c.title+'|'+c.year).toLowerCase()));
      if(!cands.length){ res.innerHTML='<div class="t10-none">Nichts gefunden</div>'; return; }
      res.innerHTML=cands.slice(0,8).map((c,i)=>hitHTML(c.poster,c.title,c.year,'','data-x="'+i+'"')).join('');
      res.querySelectorAll('.t10-hit').forEach(h=>h.onmousedown=ev=>{
        ev.preventDefault();
        const c=cands[+h.dataset.x];
        saveTop10(kind,[...raws(),{ext:true,kind,title:c.title,year:c.year,poster:c.poster}]);
      });
    },350);
  };
  q.oninput=run;
  q.onfocus=run;
  q.onblur=()=>setTimeout(()=>{res.hidden=true;},150);
}

// Vorschlag: beste Wertungen, je Titel einmal, ohne Rewatches
function top10Suggest(kind){
  const seen=new Set();
  return diary.filter(e=>e.kind===kind&&e.rating&&!e.rewatch)
    .sort((a,b)=>b.rating-a.rating||(b.date||'').localeCompare(a.date||''))
    .filter(e=>{const k=kind==='season'?top10Label(e):e.title.toLowerCase(); if(seen.has(k)) return false; seen.add(k); return true;});
}

// ── Gesamtauswertung (alle Jahre) ─────────────────────────────────────────────
let statsAllKind='all';   // all | movie | season | game
const KIND_LBL={movie:'Filme',season:'Serien',game:'Spiele'};
const KIND_COL={movie:'var(--accent2)',season:'var(--accent3)',game:'var(--accent)'};
const WEEKDAYS=['Mo','Di','Mi','Do','Fr','Sa','So'];

function statsAllToggle(){
  return `<div class="seg-toggle">${
    [['all','Alle'],['movie','Filme'],['season','Serien'],['game','Spiele']].map(([k,l])=>
      `<button class="${statsAllKind===k?'on':''}" onclick="setStatsAllKind('${k}')">${l}</button>`).join('')}</div>`;
}
// Interne Wertung ist 0.5-5 Sterne; Rankings zeigen sie als 1-10.
const fmt10=v=>String(Math.round(v*2*10)/10);
function setStatsAllKind(k){ statsAllKind=k; renderStatsAll(); }

function niceMax(m){
  const p=Math.pow(10,Math.floor(Math.log10(m))), n=m/p;
  return (n<=1?1:n<=2?2:n<=5?5:10)*p;
}

// Balkendiagramm mit Gitterlinien. cols: [{label, top, tip, segs:[{v,col,tip}]}]
function barChartHTML(cols,max,opts={}){
  const ticks=opts.ticks||4;
  const grid=Array.from({length:ticks+1},(_,i)=>{
    const v=max/ticks*(ticks-i);
    return `<div class="bc-grid" style="bottom:${(ticks-i)/ticks*100}%"><span>${opts.axisFmt?opts.axisFmt(v):Math.round(v)}</span></div>`;
  }).join('');
  const avgLine=opts.avg
    ?`<div class="bc-avg" style="bottom:${opts.avg/max*100}%"><span>Ø ${opts.avg.toFixed(2)}</span></div>`:'';
  const bars=cols.map(c=>{
    const total=c.segs.reduce((a,x)=>a+x.v,0);
    const segs=c.segs.filter(x=>x.v>0).map(x=>
      `<div class="bc-seg" style="height:${x.v/max*100}%;background:${x.col}" title="${esc(x.tip||'')}"></div>`).join('');
    return `<div class="bc-col" title="${esc(c.tip||'')}">
      <div class="bc-stack">${segs}</div>
      <span class="bc-val" style="bottom:calc(${total/max*100}% + 3px)">${c.top||''}</span>
      <span class="bc-lbl">${c.label}</span>
    </div>`;
  }).join('');
  return `<div class="bc">${grid}${avgLine}<div class="bc-cols">${bars}</div></div>`;
}

function renderStatsAll(){
  const body=document.getElementById('stats-body');
  const all=diary.filter(e=>(e.date||'').length>=4);
  if(!all.length){ body.innerHTML='<div class="dempty">Noch keine Einträge.</div>'; return; }

  const kinds=statsAllKind==='all'?['movie','season','game']:[statsAllKind];
  const ent=all.filter(e=>kinds.includes(e.kind));
  const rated=ent.filter(e=>e.rating);
  const avg=a=>a.length?a.reduce((s,e)=>s+e.rating,0)/a.length*2:0;   // 10er-Skala
  const overall=avg(rated);

  if(!ent.length){
    body.innerHTML=`<div class="stats-toolbar">${statsAllToggle()}</div><div class="dempty">Keine Einträge für diese Auswahl.</div>`;
    return;
  }
  const years=[...new Set(ent.map(e=>e.date.slice(0,4)))].sort();
  const minY=+years[0], maxY=+years[years.length-1];
  const span=[]; for(let y=minY;y<=maxY;y++) span.push(String(y));   // auch Jahre ohne Einträge

  const perYear=y=>ent.filter(e=>e.date.startsWith(y));
  const cnt=span.map(y=>perYear(y).length);
  const avgs=span.map(y=>avg(perYear(y).filter(e=>e.rating)));

  // Kennzahlen
  const bestCnt=Math.max(...cnt), busiestYear=span[cnt.indexOf(bestCnt)];
  const ratedYears=span.filter((y,i)=>avgs[i]>0&&perYear(y).filter(e=>e.rating).length>=5);
  const bestYear=ratedYears.length
    ?ratedYears.reduce((a,b)=>avgs[span.indexOf(b)]>avgs[span.indexOf(a)]?b:a):null;
  const rewatch=ent.filter(e=>e.rewatch).length;
  const perKind=k=>ent.filter(e=>e.kind===k).length;
  const fiveStar=rated.filter(e=>e.rating>=5).length;

  const toggle=statsAllToggle();

  // Anzahl pro Jahr (bei "Alle" gestapelt nach Medium)
  const cMax=niceMax(Math.max(...cnt,1));
  const cntChart=barChartHTML(span.map((y,i)=>({
    label:y.slice(2), top:cnt[i]||'',
    tip:`${y}: ${cnt[i]} Einträge`,
    segs:kinds.map(k=>{
      const n=perYear(y).filter(e=>e.kind===k).length;
      return {v:n,col:KIND_COL[k],tip:`${y} · ${KIND_LBL[k]}: ${n}`};
    })
  })),cMax,{ticks:cMax>=5?5:cMax});

  // Ø Bewertung pro Jahr, gestrichelte Linie = Gesamtschnitt
  const avgChart=barChartHTML(span.map((y,i)=>({
    label:y.slice(2), top:avgs[i]?avgs[i].toFixed(2):'',
    tip:`${y}: Ø ${avgs[i]?avgs[i].toFixed(2):'–'} aus ${perYear(y).filter(e=>e.rating).length} Bewertungen`,
    segs:[{v:avgs[i],col:statsAllKind==='all'?'var(--chart)':KIND_COL[statsAllKind]}]
  })),10,{ticks:5,avg:overall,axisFmt:v=>v.toFixed(0)});

  // Heatmap Jahr × Monat
  const hm=span.map(y=>{
    const m=Array(12).fill(0);
    perYear(y).forEach(e=>{const i=+e.date.slice(5,7)-1; if(i>=0&&i<12) m[i]++;});
    return m;
  });
  const hmMax=Math.max(...hm.flat(),1);
  const heat=`<div class="heat">
    <div class="heat-row heat-head"><span></span>${MSHORT.map(m=>`<span>${m}</span>`).join('')}</div>
    ${span.slice().reverse().map(y=>{
      const r=hm[span.indexOf(y)];
      return `<div class="heat-row"><span class="heat-y">${y}</span>${r.map((n,i)=>
        `<span class="heat-c" style="--t:${n?(0.18+0.82*n/hmMax).toFixed(2):0}" title="${MONTHS[i]} ${y}: ${n}">${n||''}</span>`).join('')}</div>`;
    }).join('')}</div>`;

  // Bewertungsverteilung
  const dist={}; for(let v=0.5;v<=5;v+=0.5) dist[v]=0;
  rated.forEach(e=>{ if(dist[e.rating]!==undefined) dist[e.rating]++; });
  const maxD=Math.max(...Object.values(dist),1);
  const distHTML=`<div class="rdist" style="height:110px">${Object.entries(dist).map(([v,n])=>`
    <div class="rcol"><span class="mval">${n||''}</span>
    <div class="rbar" style="height:${n/maxD*84}px" title="${n} × ${fmt10(+v)}/10"></div>
    <span class="rlbl">${fmt10(+v)}</span></div>`).join('')}</div>`;

  // Genres mit Ø-Bewertung
  const gc={};
  ent.forEach(e=>(e.genre||'').split(',').map(g=>g.trim()).filter(Boolean).forEach(g=>{
    const o=gc[g]||(gc[g]={n:0,sum:0,r:0}); o.n++; if(e.rating){o.sum+=e.rating;o.r++;}}));
  const genres=Object.entries(gc).sort((a,b)=>b[1].n-a[1].n).slice(0,10);
  const maxG=genres.length?genres[0][1].n:1;
  const genHTML=genres.map(([g,o])=>`<div class="genrow">
    <span class="genname">${esc(genreLabel(g))}</span>
    <div class="genbar-bg"><div class="genbar" style="width:${o.n/maxG*100}%"></div></div>
    <span class="genval">${o.n}</span>
    <span class="genavg" title="Ø Bewertung">${o.r?'Ø '+(o.sum/o.r*2).toFixed(1):''}</span></div>`).join('');

  // Wochentage
  const wd=Array(7).fill(0);
  ent.forEach(e=>{
    if(e.date.length>=10){
      const d=new Date(e.date.slice(0,10)+'T12:00:00');
      if(!isNaN(d)) wd[(d.getDay()+6)%7]++;
    }
  });
  const maxW=Math.max(...wd,1);
  const wdHTML=`<div class="months" style="grid-template-columns:repeat(7,1fr)">${wd.map((n,i)=>`
    <div class="mcell"><span class="mval">${n||''}</span>
    <div class="mbar-wrap"><div class="mbar" style="height:${n/maxW*100}%"></div></div>
    <span class="mlbl">${WEEKDAYS[i]}</span></div>`).join('')}</div>`;

  // Jahrzehnte der Titel (Erscheinungsjahr)
  const dec={};
  ent.forEach(e=>{const y=parseInt(e.year); if(y>=1900){const d=Math.floor(y/10)*10; dec[d]=(dec[d]||0)+1;}});
  const decs=Object.entries(dec).sort((a,b)=>a[0]-b[0]);
  const maxDec=Math.max(...decs.map(d=>d[1]),1);
  const decHTML=decs.length?`<div class="months" style="grid-template-columns:repeat(${decs.length},1fr)">${decs.map(([d,n])=>`
    <div class="mcell"><span class="mval">${n}</span>
    <div class="mbar-wrap"><div class="mbar" style="height:${n/maxDec*100}%"></div></div>
    <span class="mlbl">${d}er</span></div>`).join('')}</div>`:'<div class="aw-empty">Keine Erscheinungsjahre vorhanden</div>';

  // Bestenliste aller Zeiten (ohne Rewatches; Filme/Spiele nur einmal)
  const seen=new Set();
  const top=rated.filter(e=>!e.rewatch)
    .sort((a,b)=>b.rating-a.rating||b.date.localeCompare(a.date))
    .filter(e=>{
      if(e.kind==='season') return true;
      const k=(e.key||e.title)+'|'+e.kind;
      if(seen.has(k)) return false; seen.add(k); return true;
    }).slice(0,10);
  const topHTML=top.map((e,i)=>`<div class="toprow">
    <span class="toprank">${i+1}</span>
    ${e.poster?`<img class="top-cover" src="${e.poster}" alt="" loading="lazy">`:'<span class="top-cover"></span>'}
    <span class="topname">${esc(e.title)}${e.kind==='season'?` <span class="tsub">S${String(e.season||1).padStart(2,'0')}</span>`:''}
      <span class="tsub">· ${e.date.slice(0,4)}</span></span>
    <span class="r10">${fmt10(e.rating)}</span></div>`).join('');

  // Gewinner je Jahr
  const winnerCell=(y,cat,kind)=>{
    const id=(awards[y]||{})[cat], e=id&&diary.find(x=>x.id===id);
    if(!e) return '<td class="win-none">–</td>';
    const sub=kind==='season'?` <span class="tsub">S${String(e.season||1).padStart(2,'0')}</span>`:'';
    return `<td>${esc(e.title)}${sub}</td>`;
  };
  const winCats={movie:'f_best',season:'s_best',game:'g_best'};
  const winYears=span.slice().reverse().filter(y=>kinds.some(k=>(awards[y]||{})[winCats[k]]));
  const winnersHTML=winYears.length?`<table class="win-table"><thead><tr><th>Jahr</th>
      ${kinds.includes('movie')?'<th>Bester Film</th>':''}${kinds.includes('season')?'<th>Beste Serie</th>':''}${kinds.includes('game')?'<th>Bestes Spiel</th>':''}
    </tr></thead><tbody>${winYears.map(y=>`<tr><td class="win-y">${y}</td>
      ${kinds.includes('movie')?winnerCell(y,'f_best','movie'):''}
      ${kinds.includes('season')?winnerCell(y,'s_best','season'):''}
      ${kinds.includes('game')?winnerCell(y,'g_best','game'):''}</tr>`).join('')}</tbody></table>`:'';

  const legend=statsAllKind==='all'
    ?`<div class="bc-legend">${['movie','season','game'].map(k=>
        `<span><i style="background:${KIND_COL[k]}"></i>${KIND_LBL[k]}</span>`).join('')}</div>`:'';

  body.innerHTML=`
    <div class="stats-toolbar">${toggle}</div>
    <div class="stats-grid">
      <div class="scard">
        <div class="scard-lbl">Einträge gesamt</div>
        <div class="sbig">${ent.length}<span class="sunit">seit ${minY}</span></div>
        <div class="ssplit">${kinds.map(k=>`<div><b>${perKind(k)}</b>${KIND_LBL[k]}</div>`).join('')}</div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Ø Bewertung</div>
        <div class="sbig">${overall?overall.toFixed(2):'–'}<span class="sunit">/ 10</span></div>
        <div class="ssplit"><div><b>${rated.length}</b>bewertet</div><div><b>${fiveStar}</b>× 10/10</div></div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Aktivstes Jahr</div>
        <div class="sbig">${busiestYear}</div>
        <div class="ssplit"><div><b>${bestCnt}</b>Einträge</div></div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Bestes Jahr</div>
        <div class="sbig">${bestYear||'–'}</div>
        <div class="ssplit">${bestYear?`<div><b>Ø ${avgs[span.indexOf(bestYear)].toFixed(2)}</b>ab 5 Bewertungen</div>`:''}</div>
      </div>
      <div class="scard">
        <div class="scard-lbl">Erneut gesehen</div>
        <div class="sbig">${rewatch}</div>
        <div class="ssplit"><div><b>${ent.length?Math.round(rewatch/ent.length*100):0}%</b>aller Einträge</div></div>
      </div>
    </div>

    <div class="stats-wide">
      <div class="stats-2col">
        <div class="swide-card"><div class="scard-lbl">Anzahl pro Jahr</div>${legend}${cntChart}</div>
        <div class="swide-card"><div class="scard-lbl">Ø Bewertung pro Jahr</div>${avgChart}</div>
      </div>
      <div class="swide-card"><div class="scard-lbl">Aktivität nach Monat</div>${heat}</div>
      <div class="stats-2col">
        <div class="swide-card"><div class="scard-lbl">Bewertungsverteilung</div>${distHTML}</div>
        <div class="swide-card"><div class="scard-lbl">Lieblingsgenres</div>${genHTML||'<div class="aw-empty">Keine Genres</div>'}</div>
        <div class="swide-card"><div class="scard-lbl">Wochentage</div>${wdHTML}</div>
        <div class="swide-card"><div class="scard-lbl">Jahrzehnte der Titel</div>${decHTML}</div>
      </div>
      <div class="stats-2col">
        ${top.length?`<div class="swide-card"><div class="scard-lbl">Beste Wertungen aller Zeiten</div><div class="toplist">${topHTML}</div></div>`:''}
        ${winnersHTML?`<div class="swide-card"><div class="scard-lbl">Gewinner je Jahr</div>${winnersHTML}</div>`:''}
      </div>
    </div>`;
}

// ── Awards: Nominierte je Kategorie, Gewinner per Klick ───────────────────────
function awardsHTML(year,ent,type){
  const cats=type==='film'?CATS_FILM:type==='series'?CATS_SERIES:CATS_GAMES;
  const won=awards[year]||{};
  const wantKind=type==='film'?'movie':type==='series'?'season':'game';
  const nomCount=ent.filter(e=>(e.noms||[]).length&&e.kind===wantKind).length;

  const renderCategory=c=>{
    const noms=ent.filter(e=>(e.noms||[]).includes(c.id));
    const body=noms.length
      ? noms.sort((a,b)=>(b.rating||0)-(a.rating||0)).map(e=>{
          const isWin=won[c.id]===e.id;
          const sub=e.kind==='season'?` S${String(e.season||1).padStart(2,'0')}`:'';
          const person=(c.person||c.text)?(e.nomPeople||{})[c.id]:null;
          const label=person
            ? `${esc(person.name)} <span style="color:var(--muted);font-weight:400">— ${esc(e.title)}${sub}</span>`
            : `${esc(e.title)}${sub}`;
          const icon=e.kind==='season'?'S':e.kind==='game'?'G':'F';
          // Schauspieler-Kategorien: Foto der Person (bereits heruntergeladen), sonst Filmcover
          const img=(person&&person.photo)||e.poster;
          const cover=img
            ? `<img class="aw-nom-cover" src="${img}" alt="" loading="lazy">`
            : `<div class="aw-nom-cover-ph">${icon}</div>`;
          return `<div class="aw-nom${isWin?' win':''}" data-cat="${c.id}" data-eid="${e.id}">
            <span class="aw-crown">${isWin?'✓':'○'}</span>
            ${cover}
            <span class="aw-nom-name">${label}</span>
            ${starsHTML(e.rating,true)}
          </div>`;
        }).join('')
      : '<div class="aw-empty">Keine Nominierungen</div>';
    return `<div class="aw-cat">
      <div class="aw-cat-name">${c.n}</div>
      ${c.hint?`<div class="aw-cat-hint">${c.hint}</div>`:''}
      ${body}
    </div>`;
  };
  const filled=cats.filter(c=>ent.some(e=>(e.noms||[]).includes(c.id)));
  const empty=cats.filter(c=>!filled.includes(c));

  const title=type==='film'?'Film-Auszeichnungen':type==='series'?'Serien-Auszeichnungen':'Spiele-Auszeichnungen';
  return `<div class="swide-card">
    <div class="aw-section-title">${title} ${year}
      <span class="cnt">${nomCount} nominierte Einträge${filled.length?' · Gewinner durch Klick wählen':''}</span></div>
    ${filled.length?`<div class="aw-grid">${filled.map(renderCategory).join('')}</div>`:
      '<div class="aw-empty">Noch keine Nominierungen</div>'}
    ${empty.length?`<details class="aw-more">
      <summary>${empty.length} weitere Kategorien anzeigen</summary>
      <div class="aw-grid">${empty.map(renderCategory).join('')}</div>
    </details>`:''}
  </div>`;
}

function bindAwards(year){
  document.querySelectorAll('.aw-nom').forEach(el=>{
    el.onclick=async()=>{
      const cat=el.dataset.cat, eid=el.dataset.eid;
      const cur=(awards[year]||{})[cat];
      try{
        const d=await fetch('/api/awards',{
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({year,cat,entryId:cur===eid?null:eid})
        }).then(r=>r.json());
        if(d.ok){ awards=d.awards; renderStats(); }
      }catch{ showToast('⚠ Netzwerkfehler',true); }
    };
  });
}

function setupLb(){
  document.getElementById('lbSaveBtn').onclick=saveLbUsername;
  document.getElementById('lbSyncBtn').onclick=runLbSync;
}

// ── Fehlende Cover im Hintergrund nachladen ────────────────────────────────────
// Läuft nach dem Start in kleinen Schritten, blockiert nie den Seitenaufbau.
let _coverBackfillRunning=false;
// ── Empfehlungen ───────────────────────────────────────────────────────────────
// Mehrere Algorithmen (Backend: at_recommendations.py), per Dropdown wählbar.
// ✓ = gute Empfehlung/angefangen, ✗ = nicht mein Geschmack - beides blendet den
// Titel dauerhaft aus und fließt als Signal ins Geschmacksprofil ein.
const REC_MODES={
  recent:  'Basiert auf dem, was du zuletzt gesehen und gespielt hast – je länger ein Eintrag her ist, desto weniger zählt er (Halbwertszeit 90 Tage).',
  alltime: 'Dein komplettes Tagebuch, gewichtet nach deinen Bewertungen – was du über deinem Schnitt bewertest, zählt positiv, darunter negativ.',
  because: 'Empfehlungen zu deinen zuletzt gut bewerteten Titeln, gruppiert nach Auslöser.',
  people:  'Weitere Werke von Regisseuren, Serienschöpfern, Studios und Darstellern, von denen du mehrere Titel gut fandest.',
  gems:    'Hoch bewertet, aber kaum bekannt – und trotzdem passend zu deinem Geschmack.',
  surprise:'Passend zu deinem Geschmack, aber bewusst abwechslungsreich gemischt. Bei jedem Öffnen neu.',
};
const REC_MODE_KEY='arctime_recMode';
let recMode=(()=>{ try{ const m=localStorage.getItem(REC_MODE_KEY); return REC_MODES[m]?m:'recent'; }catch{ return 'recent'; } })();
let recStatus=null, _recReq=0, _recAnimating=0, _recRefreshPending=false;

async function loadRecommendations(){
  const req=++_recReq;
  try{
    const d=await fetch('/api/recommendations?mode='+encodeURIComponent(recMode)).then(r=>r.json());
    if(req!==_recReq) return;                       // inzwischen anderer Modus gewählt
    if(d.ok){ recommendations=d.items||[]; recStatus=d.status||null; recLoaded=true; }
  }catch{ /* offline - Karten bleiben leer, kein Retry-Sturm */ }
  if(currentTab==='recommendations') renderRecommendations();
}

async function loadRecommendationsIfNeeded(){
  const sel=document.getElementById('rec-mode');
  if(sel) sel.value=recMode;
  if(recLoaded){ renderRecommendations(); }
  else await loadRecommendations();
  backfillRecommendationsLoop();
}

function setRecMode(m){
  if(!REC_MODES[m]) return;
  recMode=m;
  try{ localStorage.setItem(REC_MODE_KEY,m); }catch{}
  recLoaded=false; recommendations=[];
  renderRecommendations();
  loadRecommendations();
}

function setRecFilter(f,btn){
  recFilter=f;
  document.querySelectorAll('#section-recommendations .dfilter').forEach(b=>b.classList.toggle('active',b===btn));
  renderRecommendations();
}

function recVisibleList(){
  const q=(tabQueries.recommendations||'').trim().toLocaleLowerCase('de');
  let list=recommendations
    .filter(r=>recFilter==='all'||r.kind===recFilter)
    .filter(r=>!q||(r.title||'').toLocaleLowerCase('de').includes(q));
  const grouped=list.some(r=>r.group);
  if(!grouped){
    if(recMode==='surprise'&&recFilter==='all'){
      // MMR-Reihenfolge des Servers erhalten, Arten reihum mischen
      const by={movie:[],series:[],game:[]};
      list.forEach(r=>(by[r.kind]||(by[r.kind]=[])).push(r));
      const mixed=[]; let i=0;
      while(mixed.length<list.length){
        for(const k of ['movie','series','game']) if(by[k][i]) mixed.push(by[k][i]);
        i++;
      }
      list=mixed;
    }else if(recMode!=='surprise'){
      list=[...list].sort((a,b)=>b.match-a.match);
    }
  }
  return {list,grouped};
}

function updateRecStatus(){
  document.getElementById('rec-mode-desc').textContent=REC_MODES[recMode]||'';
  const prog=document.getElementById('rec-progress');
  const s=recStatus;
  if(s&&s.seeds_total&&s.seeds_done<s.seeds_total){
    prog.hidden=false;
    const pct=Math.round(100*s.seeds_done/s.seeds_total);
    document.getElementById('rec-progress-fill').style.width=pct+'%';
    document.getElementById('rec-progress-label').textContent=
      `Geschmacksprofil wird aufgebaut: ${s.seeds_done} von ${s.seeds_total} Tagebuch-Titeln analysiert`;
  }else prog.hidden=true;
}

function updateRecCount(){
  const {list}=recVisibleList();
  document.getElementById('rec-count').textContent=
    recLoaded?(list.length+' Empfehlung'+(list.length===1?'':'en')):'Lädt…';
}

function renderRecommendations(){
  const grid=document.getElementById('rec-grid');
  const {list,grouped}=recVisibleList();
  updateRecCount();
  updateRecStatus();

  if(!list.length){
    const q=(tabQueries.recommendations||'').trim();
    let msg='Lädt…';
    if(recLoaded){
      if(q) msg=`Keine Empfehlungen für „${esc(q)}“ gefunden.`;
      else if(recStatus&&!recStatus.tmdb) msg='Kein TMDB-API-Key konfiguriert – ohne ihn können keine Empfehlungen berechnet werden.';
      else if(recStatus&&recStatus.seeds_done<recStatus.seeds_total&&!recommendations.length)
        msg='Dein Geschmacksprofil wird gerade aufgebaut – die ersten Empfehlungen erscheinen gleich.';
      else msg='Keine Empfehlungen in diesem Filter.';
    }
    grid.innerHTML=`<div class="dempty">${msg}</div>`;
    return;
  }
  if(!grouped){
    grid.innerHTML=`<div class="grid">${list.map(recCard).join('')}</div>`;
    return;
  }
  const groups=[], byLabel={};
  for(const r of list){
    const g=r.group||'Weitere';
    if(!byLabel[g]){ byLabel[g]=[]; groups.push(g); }
    byLabel[g].push(r);
  }
  grid.innerHTML=groups.map(g=>`<section class="rec-group">
      <h3 class="rec-group-title">${esc(g)}</h3>
      <div class="grid">${byLabel[g].map(recCard).join('')}</div>
    </section>`).join('');
}

const LETTERBOXD_MARK=`<svg viewBox="0 0 64 32" width="26" height="13" aria-hidden="true">
  <circle cx="16" cy="16" r="16" fill="#ff8000"/>
  <circle cx="32" cy="16" r="16" fill="#00e054" opacity=".88"/>
  <circle cx="48" cy="16" r="16" fill="#40bcf4" opacity=".88"/>
</svg>`;

function recExtLink(r){
  const id=(r.key||'').split(':')[1];
  if(r.kind==='movie') return {href:`https://letterboxd.com/tmdb/${id}`,inner:LETTERBOXD_MARK,title:'Letterboxd',mark:true};
  if(r.kind==='series'){
    // IMDb statt TMDB: direkt per IMDb-ID, sonst IMDb-Suche
    const href=r.imdb_id?`https://www.imdb.com/title/${r.imdb_id}/`
      :`https://www.imdb.com/find/?s=tt&q=${encodeURIComponent(r.title+(r.year?' '+r.year:''))}`;
    return {href,inner:'IMDb',title:'IMDb'};
  }
  return {href:`https://www.igdb.com/search?type=1&q=${encodeURIComponent(r.title)}`,inner:'IGDB',title:'IGDB'};
}

function recCard(r){
  const poster=r.poster
    ?`<img src="${esc(r.poster)}" alt="${esc(r.title)}" loading="lazy">`
    :`<div class="pp"><span>${esc(r.title)}</span></div>`;
  const kindLbl=r.kind==='movie'?'Film':r.kind==='series'?'Serie':'Spiel';
  const rc=r.match>=80?'high':r.match>=60?'mid':'low';
  const l=recExtLink(r);
  const k=esc(r.key);
  return `<div class="card rec-card" data-key="${k}" onclick="openRecModal('${k}')">
    <div class="pw">${poster}<div class="rb rec-match ${rc}" title="Passt zu deinem Geschmack">${r.match}%</div>
      <div class="rec-actions">
        <button class="rec-fb-btn dislike" title="Nicht mein Geschmack – nicht mehr empfehlen"
          onclick="recFeedback(event,'${k}','dislike')"><i class="fa-solid fa-xmark"></i></button>
        <button class="rec-fb-btn quality" title="Thema passt, aber qualitativ nicht gut genug"
          onclick="recFeedback(event,'${k}','quality')"><i class="fa-solid fa-star-half-stroke"></i></button>
        <button class="rec-fb-btn like" title="Gute Empfehlung – angefangen"
          onclick="recFeedback(event,'${k}','like')"><i class="fa-solid fa-check"></i></button>
      </div>
    </div>
    <div class="cb">
      <div class="rec-head">
        <div class="ct">${esc(r.title)}</div>
        <a class="rec-link${l.mark?' rec-link-mark':''}" href="${l.href}" target="_blank" title="${l.title}" onclick="event.stopPropagation()">${l.inner}</a>
      </div>
      <div class="cm">${esc(r.year||'')}${r.genre?' · '+esc(genreLabel(r.genre.split(',')[0].trim())):''} · ${kindLbl}</div>
      <div class="rec-hook">${esc(r.hook)}</div>
    </div>
  </div>`;
}

let _recModalKey=null;
function openRecModal(key){
  const r=recommendations.find(x=>x.key===key);
  if(!r) return;
  _recModalKey=key;
  const kindLbl=r.kind==='movie'?'Film':r.kind==='series'?'Serie':'Spiel';
  document.getElementById('rm-poster').innerHTML=r.poster
    ?`<img src="${esc(r.poster)}" alt="${esc(r.title)}">`
    :`<div class="pp"><span>${esc(r.title)}</span></div>`;
  document.getElementById('rm-title').textContent=r.title||'—';
  document.getElementById('rm-year').textContent=[r.year,kindLbl,r.match?r.match+'% Match':''].filter(Boolean).join(' · ');

  const rEl=document.getElementById('rm-rating');
  if(r.rating){
    rEl.className='mrating '+rCls(r.rating);
    rEl.innerHTML=`${(+r.rating).toFixed(1)} <span>/ 10${r.votes?' · '+r.votes+' Stimmen':''}</span>`;
    rEl.style.display='';
  }else rEl.style.display='none';
  document.getElementById('rm-genres').innerHTML=(r.genre||'').split(',').map(g=>g.trim()).filter(Boolean)
    .map(g=>`<span class="gp">${esc(genreLabel(g))}</span>`).join('');
  document.getElementById('rm-why').textContent=r.hook||'';
  document.getElementById('rm-plot').textContent=r.plot||'Keine Beschreibung verfügbar.';

  const metaItems=[
    r.runtime?[r.kind==='series'?'Episodenlänge':'Laufzeit', r.runtime+' Min.']:null,
    r.total_seasons?['Staffeln', r.total_seasons]:null,
    (r.platforms&&r.platforms.length)?['Plattformen', r.platforms.join(', ')]:null,
  ].filter(Boolean);
  document.getElementById('rm-meta').innerHTML=metaItems
    .map(([l,v])=>`<div class="mi2"><span class="ml">${esc(l)}</span><span class="mv">${esc(String(v))}</span></div>`).join('');

  const l=recExtLink(r);
  document.getElementById('rm-links').innerHTML=`<a class="mlink" href="${l.href}" target="_blank">Mehr Infos (${l.title})</a>`;

  document.getElementById('recMo').classList.add('open');
  document.body.style.overflow='hidden';
}
function closeRecModal(){
  document.getElementById('recMo').classList.remove('open');
  document.body.style.overflow='';
}
function bgCloseRec(e){ if(e.target.id==='recMo') closeRecModal(); }

function recFeedbackFromModal(verdict){
  const key=_recModalKey;
  closeRecModal();
  const card=[...document.querySelectorAll('#rec-grid .rec-card')].find(c=>c.dataset.key===key);
  const btn=card&&card.querySelector('.rec-fb-btn.'+verdict);
  // kurz warten, bis das Modal ausgeblendet ist, damit man die Animation sieht
  setTimeout(()=>{
    if(btn) recFeedback({stopPropagation(){}, currentTarget:btn}, key, verdict);
    else { sendRecFeedback(key,verdict); recommendations=recommendations.filter(r=>r.key!==key); renderRecommendations(); }
  },220);
}

function sendRecFeedback(key,verdict){
  fetch('/api/recommendations/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key,verdict})})
    .then(r=>r.json()).then(d=>{ if(!d.ok) showToast('⚠ Feedback konnte nicht gespeichert werden',true); })
    .catch(()=>showToast('⚠ Netzwerkfehler',true));
}

async function recFeedback(ev,key,verdict){
  ev.stopPropagation();
  const btn=ev.currentTarget;
  const card=btn&&btn.closest('.rec-card');
  if(!card||card.classList.contains('fb-active')) return;
  sendRecFeedback(key,verdict);
  _recAnimating++;
  try{ await animateRecFeedback(card,btn,verdict); }
  catch{ card.remove(); }
  finally{ _recAnimating--; }
  recommendations=recommendations.filter(r=>r.key!==key);
  updateRecCount();
  if(!document.querySelector('#rec-grid .rec-card')) renderRecommendations();
  if(_recRefreshPending&&!_recAnimating){ _recRefreshPending=false; loadRecommendations(); }
}

const _sleep=ms=>new Promise(res=>setTimeout(res,ms));
const REC_FB_ICON={like:'fa-check',quality:'fa-star-half-stroke',dislike:'fa-xmark'};

// Ablauf: Icon fliegt vom Button in die Kartenmitte und wächst, dahinter färbt
// sich die Karte grün/rot, ein Ring pulsiert -> Karte schrumpft und blendet aus
// -> die übrigen Karten gleiten an ihre neuen Plätze (FLIP).
async function animateRecFeedback(card,btn,verdict){
  const reduced=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cr=card.getBoundingClientRect(), br=btn.getBoundingClientRect();
  const dx=(br.left+br.width/2)-(cr.left+cr.width/2);
  const dy=(br.top+br.height/2)-(cr.top+cr.height/2);

  const ov=document.createElement('div');
  ov.className='rec-fb-overlay '+verdict;
  ov.innerHTML=`<div class="rec-fb-bg"></div><div class="rec-fb-ring"></div>
    <div class="rec-fb-icon"><i class="fa-solid ${REC_FB_ICON[verdict]||'fa-xmark'}"></i></div>`;
  card.appendChild(ov);
  card.classList.add('fb-active');
  const bg=ov.querySelector('.rec-fb-bg'), ring=ov.querySelector('.rec-fb-ring'), icon=ov.querySelector('.rec-fb-icon');

  if(reduced){
    bg.style.opacity=1;
    await card.animate([{opacity:1},{opacity:0}],{duration:250,delay:250,fill:'forwards'}).finished;
  }else{
    const ease='cubic-bezier(.2,.8,.2,1)';
    const aIcon=icon.animate([
      {transform:`translate(${dx}px,${dy}px) scale(.42)`,opacity:.9},
      {transform:'translate(0,0) scale(1.18)',opacity:1,offset:.68},
      {transform:'translate(0,0) scale(1)',opacity:1}
    ],{duration:560,easing:ease,fill:'forwards'});
    bg.animate([{opacity:0},{opacity:1}],{duration:380,easing:'ease-out',fill:'forwards'});
    ring.animate([
      {transform:'scale(.5)',opacity:0},
      {transform:'scale(.5)',opacity:.7,offset:.35},
      {transform:'scale(1.9)',opacity:0}
    ],{duration:760,easing:'ease-out',fill:'forwards'});
    card.animate([{transform:'scale(1)'},{transform:'scale(1.035)'},{transform:'scale(1)'}],
      {duration:520,easing:ease});
    await aIcon.finished;
    await _sleep(260);
    await card.animate([
      {transform:'scale(1)',opacity:1},
      {transform:'scale(.8)',opacity:0}
    ],{duration:320,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'}).finished;
  }

  // FLIP: Positionen vorher merken, Karte entfernen, Rest sanft nachrutschen lassen
  const grid=document.getElementById('rec-grid');
  const others=[...grid.querySelectorAll('.rec-card:not(.fb-active), .rec-group-title')];
  const first=new Map(others.map(el=>[el,el.getBoundingClientRect()]));
  const group=card.closest('.rec-group');
  card.remove();
  if(group&&!group.querySelector('.rec-card')) group.remove();
  if(reduced) return;
  for(const el of others){
    if(!el.isConnected) continue;
    const a=first.get(el), b=el.getBoundingClientRect();
    const mx=a.left-b.left, my=a.top-b.top;
    if(Math.abs(mx)<1&&Math.abs(my)<1) continue;
    el.animate([{transform:`translate(${mx}px,${my}px)`},{transform:'translate(0,0)'}],
      {duration:440,easing:'cubic-bezier(.2,.8,.2,1)'});
  }
  await _sleep(440);
}

// Nachladen im Hintergrund: Seeds (Tagebuch-Titel) -> TMDB/IGDB-Empfehlungen,
// Keywords/Credits der Top-Kandidaten, Werklisten deiner Lieblings-Macher.
let _recBackfillRunning=false;
async function backfillRecommendationsLoop(){
  if(_recBackfillRunning) return;
  _recBackfillRunning=true;
  let lastPct=-1;
  try{
    for(let i=0;i<500;i++){
      const d=await fetch('/api/recommendations/backfill',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({budget:24})
      }).then(r=>r.json());
      if(!d.ok||d.offline) break;
      if(d.busy){ await _sleep(1500); continue; }
      recStatus=d;
      if(currentTab==='recommendations') updateRecStatus();
      const pct=d.seeds_total?Math.floor(100*d.seeds_done/d.seeds_total/20)*20:100;
      const done=d.remaining<=0;
      // Liste nur bei Meilensteinen neu laden (oder solange sie noch leer ist),
      // damit Karten nicht ständig unter dem Mauszeiger umsortiert werden.
      if(d.processed>0&&(pct!==lastPct||!recommendations.length||done)){
        lastPct=pct;
        if(_recAnimating) _recRefreshPending=true;
        else await loadRecommendations();
      }
      if(done) break;
    }
  }catch{ /* offline oder kein Netzwerk - still abbrechen, kein Retry-Sturm */ }
  finally{ _recBackfillRunning=false; }
}

async function backfillCoversLoop(){
  if(_coverBackfillRunning) return;
  _coverBackfillRunning=true;
  try{
    for(let i=0;i<50;i++){
      const d=await fetch('/api/diary/backfill-covers',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({limit:15})
      }).then(r=>r.json());
      if(!d.ok) break;
      if(d.updated>0){
        diary=d.entries;
        if(currentTab==='diary') renderDiary();
        if(currentTab==='stats') renderStats();
      }
      if(d.remaining<=0) break;
    }
  }catch{ /* offline oder kein Netzwerk - still abbrechen, kein Retry-Sturm */ }
  finally{ _coverBackfillRunning=false; }
}

setupImport();
setupLb();
init().then(()=>{ silentLetterboxdSync(); backfillCoversLoop(); });

// ── Releases: Erinnerungen, Glocke, Push ──────────────────────────────────────
let relState={alerts:[],notifications:[],last_check:null,push:{available:false,subscriptions:0,key:null}};
let relMedia='all', relEditing=null, relSug={status:'idle',items:[]}, relSeenNotifs=null, relSearchTimer=null, relSearchToken=0, relLoaded=false;
const REL_COUNTRY={US:'USA',DE:'Deutschland'};

async function loadReleases(silent){
  try{
    const d=await fetch('/api/releases').then(r=>r.json());
    applyRelState(d,silent);
  }catch{}
}

function applyRelState(d,silent){
  if(!d||!d.alerts) return;
  const prev=relSeenNotifs;
  relState=d; relLoaded=true;
  const ids=new Set(d.notifications.map(n=>n.id));
  // neue Meldungen, während die Seite offen ist -> Hinweis
  if(prev&&!silent){
    d.notifications.filter(n=>!prev.has(n.id)&&!n.read).forEach(n=>
      showToast(`🔔 ${n.title}${n.rest||' ist jetzt erhältlich'}`));
  }
  relSeenNotifs=ids;
  renderBell();
  if(currentTab==='releases') renderReleases();
}

// ── Datum-Helfer ──────────────────────────────────────────────────────────────
function relFmtDate(iso){
  if(!iso) return '';
  const d=new Date(iso.slice(0,10)+'T12:00:00');
  return d.toLocaleDateString('de-DE',{day:'numeric',month:'long',year:'numeric'});
}
function relDays(iso){
  const t=new Date(); t.setHours(12,0,0,0);
  return Math.round((new Date(iso.slice(0,10)+'T12:00:00')-t)/86400000);
}
function relWhen(iso){
  const n=relDays(iso);
  if(n===0) return 'heute';
  if(n===1) return 'morgen';
  if(n===-1) return 'gestern';
  if(n>1) return `in ${n} Tagen`;
  return `vor ${-n} Tagen`;
}
function relSource(r){
  if(!r) return '';
  if(r.country) return `${r.type} · ${REL_COUNTRY[r.country]||r.country}`;
  return r.type==='Eigener Termin'?'Von dir eingetragen':r.type;
}
function relAgo(iso){
  if(!iso) return 'noch nie';
  const d=new Date(iso), n=relDays(iso.slice(0,10));
  const hm=d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
  return (n===0?'heute':n===-1?'gestern':relFmtDate(iso))+', '+hm+' Uhr';
}

// ── Glocke ────────────────────────────────────────────────────────────────────
function relUnread(){ return relState.notifications.filter(n=>!n.read).length; }

function renderBell(){
  const badge=document.getElementById('bellBadge');
  if(!badge) return;
  const n=relUnread();
  badge.hidden=!n; badge.textContent=n>9?'9+':n;
  document.getElementById('bellBtn').classList.toggle('has-new',n>0);
  if(!document.getElementById('bellPanel').hidden) renderBellPanel();
}

function toggleBell(ev){
  ev.stopPropagation();
  const p=document.getElementById('bellPanel'), open=p.hidden;
  p.hidden=!open;
  document.getElementById('bellBtn').setAttribute('aria-expanded',String(open));
  if(open){ renderBellPanel(); loadReleases(true); }
}
function closeBell(){
  const p=document.getElementById('bellPanel');
  if(p&&!p.hidden){ p.hidden=true; document.getElementById('bellBtn').setAttribute('aria-expanded','false'); }
}
document.addEventListener('click',ev=>{ if(!ev.target.closest('.bell-wrap')) closeBell(); });
document.addEventListener('keydown',ev=>{ if(ev.key==='Escape') closeBell(); });

function renderBellPanel(){
  const p=document.getElementById('bellPanel');
  const ns=relState.notifications.slice(0,12);
  const pending=relState.alerts.filter(a=>!a.released).length;
  const items=ns.map(n=>`
    <button class="bell-item${n.read?'':' unread'}" onclick="openRelease('${n.id}')">
      ${n.poster?`<img src="${n.poster}" alt="">`:'<span class="bell-ph"></span>'}
      <span class="bell-txt">
        <b>${esc(n.title)}</b>${esc(n.rest||' ist jetzt erhältlich')}
        <small>${esc(n.sub||relSource(n))} · ${relWhen(n.date)}</small>
      </span>
      ${n.read?'':'<i class="bell-dot"></i>'}
    </button>`).join('');
  p.innerHTML=`
    <div class="bell-head">
      <span>Benachrichtigungen</span>
      ${relUnread()?'<button class="bell-link" onclick="markAllRead()">Alle gelesen</button>':''}
    </div>
    <div class="bell-list">${items||`<div class="bell-empty">
        <i class="fa-regular fa-bell"></i>
        <b>Keine Meldungen</b>
        <span>${pending?`Du wirst benachrichtigt, sobald einer deiner ${pending} vorgemerkten Filme erscheint.`
          :'Merke Filme im Tab „Releases“ vor – hier erscheint dann eine Meldung, sobald sie auf Blu-ray oder als VoD erhältlich sind.'}</span>
      </div>`}</div>
    <div class="bell-foot">
      ${pushToggleHTML()}
      <button class="bell-link" onclick="closeBell();switchTab('releases')">Alle Erinnerungen ansehen →</button>
    </div>`;
}

async function openRelease(nid){
  await relPost('/api/releases/read',{id:nid});
  closeBell(); switchTab('releases');
}
async function markAllRead(){ await relPost('/api/releases/read',{id:'all'}); }

async function relPost(url,body){
  try{
    const d=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}).then(r=>r.json());
    applyRelState(d,true);
    return d;
  }catch{ showToast('⚠ Server nicht erreichbar',true); return null; }
}

// ── Browser-Push ──────────────────────────────────────────────────────────────
let relPushOn=false;
function pushSupported(){ return 'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window; }

async function relRefreshPushState(){
  relPushOn=false;
  if(pushSupported()&&window.isSecureContext){
    try{
      const reg=await navigator.serviceWorker.getRegistration('/');
      const sub=reg&&await reg.pushManager.getSubscription();
      relPushOn=!!sub&&Notification.permission==='granted';
    }catch{}
  }
}

function pushToggleHTML(){
  let msg='', dis='';
  if(!relState.push.available){ msg='Push-Modul fehlt (pip install pywebpush)'; dis=' disabled'; }
  else if(!pushSupported()){ msg='Dieser Browser unterstützt keine Push-Nachrichten'; dis=' disabled'; }
  else if(!window.isSecureContext){ msg='Nur über http://localhost:8765 verfügbar'; dis=' disabled'; }
  else if(Notification.permission==='denied'){ msg='Im Browser blockiert – in den Seiteneinstellungen erlauben'; dis=' disabled'; }
  else msg=relPushOn?'Du erhältst Meldungen auch bei geschlossener Seite':'Meldungen auch bei geschlossener Seite erhalten';
  return `<label class="push-toggle${dis?' off':''}">
      <span class="push-txt"><b>Push-Benachrichtigungen</b><small>${msg}</small></span>
      <span class="switch"><input type="checkbox" ${relPushOn?'checked':''}${dis} onchange="setPush(this.checked,this)"><i></i></span>
    </label>`;
}

function b64ToU8(b){
  const pad='='.repeat((4-b.length%4)%4);
  const s=atob((b+pad).replace(/-/g,'+').replace(/_/g,'/'));
  return Uint8Array.from(s,c=>c.charCodeAt(0));
}

async function setPush(on,box){
  try{
    const reg=await navigator.serviceWorker.register('/sw.js',{scope:'/'});
    await navigator.serviceWorker.ready;
    if(on){
      const perm=await Notification.requestPermission();
      if(perm!=='granted'){ showToast('Benachrichtigungen wurden nicht erlaubt',true); relPushOn=false; return refreshPushUI(); }
      const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64ToU8(relState.push.key)});
      await relPost('/api/push/subscribe',{subscription:sub.toJSON()});
      relPushOn=true;
      showToast('✓ Push-Benachrichtigungen aktiviert');
    }else{
      const sub=await reg.pushManager.getSubscription();
      if(sub){ await relPost('/api/push/unsubscribe',{endpoint:sub.endpoint}); await sub.unsubscribe(); }
      relPushOn=false;
      showToast('Push-Benachrichtigungen deaktiviert');
    }
  }catch(e){
    showToast('⚠ Push konnte nicht eingerichtet werden',true);
    await relRefreshPushState();
  }
  refreshPushUI();
}
function refreshPushUI(){
  if(!document.getElementById('bellPanel').hidden) renderBellPanel();
  if(currentTab==='releases') renderReleases();
}

// ── Releases-Tab ──────────────────────────────────────────────────────────────
function renderReleases(){
  const body=document.getElementById('releases-body');
  if(!body) return;
  // Suchfeld nicht neu aufbauen, während getippt wird
  let bar=document.getElementById('rel-bar');
  if(!bar){
    body.innerHTML=`<div id="rel-bar"></div><div id="rel-list"></div><section class="rel-sec rel-sug" id="rel-sug"></section>`;
    bar=document.getElementById('rel-bar');
    bar.innerHTML=`
      <div class="seg-toggle rel-media" role="group" aria-label="Anzeigen"><button class="on" data-m="all" onclick="setRelMedia('all')">Alle</button><button data-m="movie" onclick="setRelMedia('movie')">Filme</button><button data-m="tv" onclick="setRelMedia('tv')">Serien</button></div>
      <div class="rel-search">
        <i class="fa-solid fa-magnifying-glass"></i>
        <input type="text" id="rel-q" placeholder="Film oder Serie suchen und Erinnerung setzen…" autocomplete="off">
        <div class="t10-results rel-results" id="rel-res" hidden></div>
      </div>
      <div class="rel-meta" id="rel-meta"></div>`;
    bindRelSearch();
  }
  document.getElementById('rel-meta').innerHTML=`
    <span>Letzte Prüfung: <b>${relAgo(relState.last_check)}</b></span>
    <button class="bell-link" id="rel-check" onclick="relCheckNow(this)">Jetzt prüfen</button>
    <span class="rel-push">${pushToggleHTML()}</span>`;

  const allAlerts=relState.alerts;
  const alerts=relMedia==='all'?allAlerts:allAlerts.filter(a=>(a.media==='tv'?'tv':'movie')===relMedia);
  const upcoming=alerts.filter(a=>!a.released&&a.release).sort((a,b)=>a.release.date.localeCompare(b.release.date));
  const open=alerts.filter(a=>!a.released&&!a.release).sort((a,b)=>(a.theatrical||'9').localeCompare(b.theatrical||'9'));
  const done=alerts.filter(a=>a.released).sort((a,b)=>((b.release||{}).date||'').localeCompare((a.release||{}).date||''));

  const sec=(title,list,hint)=>list.length?`
    <section class="rel-sec">
      <h3>${title}<span>${list.length}</span></h3>
      ${hint?`<p class="rel-hint">${hint}</p>`:''}
      <div class="rel-grid">${list.map(relCard).join('')}</div>
    </section>`:'';

  const list=document.getElementById('rel-list');
  if(!allAlerts.length){
    list.innerHTML=`<div class="rel-empty">
      <div class="rel-empty-ico"><i class="fa-regular fa-bell"></i></div>
      <h3>Noch keine Erinnerungen</h3>
      <p>Suche oben einen Film oder eine Serie und merke sie vor.
      ArcTime prüft einmal täglich die Termine in <b>Deutschland</b> und den <b>USA</b> und meldet sich bei dir,
      sobald der Film erscheint – der frühere der beiden Termine zählt.</p>
    </div>`;
    renderRelSuggestions();
    return;
  }
  list.innerHTML=
    sec('Bald erhältlich',upcoming)+
    sec('Termin noch offen',open,'Für diese Titel ist noch kein Termin bekannt (Filme: Blu-ray/VoD, Serien: Staffelstart bzw. Finale). Die tägliche Prüfung läuft weiter.')+
    sec('Bereits erschienen',done)||`<div class="rel-sug-note">Keine ${relMedia==='tv'?'Serien':'Filme'} vorgemerkt.</div>`;
  renderRelSuggestions();
}

function lbLink(id){
  // Gleiches Letterboxd-Logo wie bei den Empfehlungen
  return id?`<a class="rel-lb" href="https://letterboxd.com/tmdb/${id}/" target="_blank" rel="noopener" title="Auf Letterboxd öffnen" aria-label="Auf Letterboxd öffnen">${LETTERBOXD_MARK}</a>`:'';
}

function relCard(a){
  let chip='', date='', sub='';
  if(a.released&&a.release){
    chip='<span class="rel-chip ok"><i class="fa-solid fa-check"></i> Erhältlich</span>';
    date=relFmtDate(a.release.date);
    sub=`${relSource(a.release)} · ${relWhen(a.release.date)}`;
  }else if(a.release){
    const n=relDays(a.release.date);
    chip=`<span class="rel-chip soon">${n<=7?'Bald':'Geplant'}</span>`;
    date=relFmtDate(a.release.date);
    sub=`${relSource(a.release)} · ${relWhen(a.release.date)}`;
  }else{
    chip='<span class="rel-chip wait">Termin offen</span>';
    date='Noch kein Termin';
    sub=a.theatrical?`Kinostart ${relFmtDate(a.theatrical)}`:'Kein Kinostart bekannt';
  }
  let kino=a.release&&a.theatrical?`<div class="rel-kino">Kinostart ${relFmtDate(a.theatrical)}</div>`:'';
  const isTv=a.media==='tv';
  if(isTv){
    const sv=a.series||{};
    if(a.released) chip=`<span class="rel-chip ok"><i class="fa-solid fa-check"></i> ${a.event==='finale'?'Finale erschienen':'Gestartet'}</span>`;
    else if(!a.release) sub=sv.state==='unannounced'?'Staffel noch nicht angekündigt':(a.event==='finale'?'Finale-Termin noch offen':'Start-Termin noch offen');
    const bits=[sv.start?`Start ${relFmtDate(sv.start)}`:'',sv.finale?`Finale ${relFmtDate(sv.finale)}`:'',sv.episodes?`${sv.aired||0}/${sv.episodes} Folgen`:''].filter(Boolean);
    kino=bits.length?`<div class="rel-kino">${bits.join(' · ')}</div>`:'';
  }
  let manual='';
  if(relEditing===a.id){
    manual=`<div class="rel-manual">
      <input type="date" id="rel-md" value="${a.manual||''}" min="2000-01-01">
      <button class="btn" onclick="relSaveManual('${a.id}')">Speichern</button>
      <button class="btn btn-ghost" onclick="relEdit(null)">Abbrechen</button>
    </div>`;
  }else if(!a.released){
    manual=a.manual
      ?`<div class="rel-manual-link"><button class="bell-link" onclick="relEdit('${a.id}')">Eigenen Termin ändern</button> · <button class="bell-link" onclick="relClearManual('${a.id}')">entfernen</button></div>`
      :`<div class="rel-manual-link"><button class="bell-link" onclick="relEdit('${a.id}')"><i class="fa-regular fa-calendar"></i> Termin selbst eintragen</button></div>`;
  }
  return `<article class="rel-card${a.released?' done':''}">
    ${a.poster?`<img class="rel-poster" src="${a.poster}" alt="" loading="lazy">`:'<div class="rel-poster ph"></div>'}
    <div class="rel-info">
      <div class="rel-top">${chip}
        <button class="rel-del" onclick="relDelete('${a.id}')" title="Erinnerung löschen" aria-label="Erinnerung für ${esc(a.title)} löschen"><i class="fa-regular fa-trash-can"></i></button>
      </div>
      <h4>${esc(a.title)}${isTv?`<small>Staffel ${a.season}</small>`:(a.year?`<small>${esc(a.year)}</small>`:'')}</h4>
      <div class="rel-date">${date}</div>
      <div class="rel-sub">${sub}</div>
      ${kino}
      ${manual}
      <div class="rel-links">${isTv?tmdbLink(a.tmdb_id):lbLink(a.tmdb_id)}</div>
    </div>
  </article>`;
}

function relEdit(id){ relEditing=id; renderReleases(); const i=document.getElementById('rel-md'); if(i) i.focus(); }
async function relSaveManual(id){
  const v=(document.getElementById('rel-md')||{}).value;
  if(!v){ showToast('Bitte ein Datum wählen',true); return; }
  relEditing=null;
  const d=await relPost('/api/releases/manual',{id,date:v});
  if(d&&d.ok) showToast('✓ Termin gespeichert'); else showToast('⚠ Datum ungültig',true);
}
async function relClearManual(id){
  await relPost('/api/releases/manual',{id,date:''});
  showToast('Eigener Termin entfernt');
}

// ── „Vielleicht gefällt dir": kommende Filme passend zum Geschmack ─────────────
const REL_SUG_MODES=[['taste','Für dich'],['recent','Aktueller Geschmack'],['people','Regie & Cast'],['surprise','Überrasch mich'],['popular','Beliebt']];
const REL_SUG_WHEN=[['all','Alle'],['coming','Kommend'],['cinema','Im Kino']];
const REL_SUG_DESC={taste:'Passend zu deinem gesamten Geschmack.',recent:'Passend zu dem, was du zuletzt gesehen und gut bewertet hast.',
  people:'Neue Filme von Machern und Darstellern, deren Werke du mehrfach gut fandest.',surprise:'Passend, aber bewusst abwechslungsreich gemischt – bei jedem Aufruf neu.',
  popular:'Die beliebtesten Filme aller TMDB-Nutzer – ohne Rücksicht auf deinen Geschmack.'};
let relSugTimer=null, relSugMode='taste', relSugWhen='all';
async function loadRelSuggestions(){
  clearTimeout(relSugTimer);
  try{
    const d=await fetch(`/api/releases/suggestions?mode=${relSugMode}&when=${relSugWhen}`).then(r=>r.json());
    relSug={status:d.status||'ready',items:d.items||[]};
  }catch{ relSug={status:'error',items:[]}; }
  renderRelSuggestions();
  if(relSug.status==='loading'&&currentTab==='releases') relSugTimer=setTimeout(loadRelSuggestions,4000);
}
function setSugMode(m){ relSugMode=m; relSug={status:'loading',items:[]}; renderRelSuggestions(); loadRelSuggestions(); }
function setSugWhen(w){ relSugWhen=w; relSug={status:'loading',items:[]}; renderRelSuggestions(); loadRelSuggestions(); }
function renderRelSuggestions(){
  const box=document.getElementById('rel-sug');
  if(!box) return;
  box.hidden=relMedia==='tv';
  if(box.hidden) return;
  const have=new Set(relState.alerts.filter(a=>a.media!=='tv').map(a=>a.tmdb_id));
  const items=relSug.items.filter(i=>!have.has(i.tmdb_id));
  let inner;
  if(relSug.status==='loading'&&!items.length) inner='<div class="rel-sug-note"><span class="spin">↻</span> Suche passende Filme…</div>';
  else if(!items.length) inner='<div class="rel-sug-note">Gerade nichts Passendes gefunden – versuch eine andere Kategorie.</div>';
  else inner='<div class="rel-sug-row">'+items.map((i,n)=>`
    <article class="sug-card">
      <button class="sug-x" onclick="sugDismiss(${n})" title="Nicht mehr anzeigen" aria-label="${esc(i.title)} nicht mehr anzeigen"><i class="fa-solid fa-xmark"></i></button>
      ${i.poster?`<img src="${i.poster}" alt="" loading="lazy">`:'<div class="sug-ph"></div>'}
      <div class="sug-body">
        <span class="sug-match">${relSugMode==='popular'?'Beliebt':i.match+'% Match'}</span>
        <h4>${esc(i.title)}<small>${esc(i.year||'')}</small></h4>
        <div class="sug-date">${i.in_cinemas?'Im Kino seit '+relFmtDate(i.date):'Kinostart '+relFmtDate(i.date)}</div>
        <p>${esc(i.group||i.hook||'')}</p>
        <div class="sug-actions">
          <button class="rel-add" onclick="sugAdd(${n})"><i class="fa-regular fa-bell"></i> Erinnern</button>
          ${lbLink(i.tmdb_id)}
        </div>
      </div>
    </article>`).join('')+'</div>';
  const chips=(list,cur,fn)=>list.map(([k,l])=>`<button class="rel-chipbtn${cur===k?' on':''}" onclick="${fn}('${k}')">${l}</button>`).join('');
  box.innerHTML=`<h3>Vielleicht gefällt dir<span>${REL_SUG_MODES.find(m=>m[0]===relSugMode)[1]}</span></h3>
    <div class="rel-filters">
      <div class="rel-chips">${chips(REL_SUG_MODES,relSugMode,'setSugMode')}</div>
      <div class="rel-chips">${chips(REL_SUG_WHEN,relSugWhen,'setSugWhen')}</div>
    </div>
    <p class="rel-hint">${REL_SUG_DESC[relSugMode]} Mit „Erinnern“ wirst du benachrichtigt, sobald ein Film auf Blu-ray oder als VoD erscheint.</p>${inner}`;
  box._items=items;
}
async function sugAdd(n){
  const i=(document.getElementById('rel-sug')._items||[])[n];
  if(!i) return;
  await relAdd({tmdb_id:i.tmdb_id,title:i.title,year:i.year,poster:i.poster});
  renderRelSuggestions();
}
async function sugDismiss(n){
  const box=document.getElementById('rel-sug');
  const i=(box._items||[])[n];
  if(!i) return;
  relSug.items=relSug.items.filter(x=>x.tmdb_id!==i.tmdb_id);
  renderRelSuggestions();
  const post=undo=>fetch('/api/releases/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tmdb_id:i.tmdb_id,undo})});
  try{ await post(false); }catch{}
  showToast(`„${i.title}“ wird nicht mehr vorgeschlagen`,false,async()=>{ try{ await post(true); }catch{} loadRelSuggestions(); });
}

async function relCheckNow(btn){
  btn.disabled=true; btn.textContent='Prüfe…';
  const d=await relPost('/api/releases/check');
  if(d&&d.ok) showToast(d.new?`🔔 ${d.new} neue Meldung${d.new>1?'en':''}`:'✓ Alles geprüft – nichts Neues');
  if(d&&d.errors) showToast('⚠ TMDB war für einige Filme nicht erreichbar',true);
}

async function relDelete(id){
  const a=relState.alerts.find(x=>x.id===id);
  if(!a) return;
  const backup={tmdb_id:a.tmdb_id,title:a.title,year:a.year,poster:a.poster,media:a.media,season:a.season,event:a.event};
  try{
    const d=await fetch('/api/releases?id='+encodeURIComponent(id),{method:'DELETE'}).then(r=>r.json());
    applyRelState(d,true);
    showToast(`Erinnerung für „${a.title}“ gelöscht`,false,()=>relAdd(backup,true));
  }catch{ showToast('⚠ Löschen fehlgeschlagen',true); }
}

async function relAdd(c,undo){
  const d=await relPost('/api/releases/add',{tmdb_id:c.tmdb_id,title:c.title,year:c.year,poster:c.poster,media:c.media,season:c.season,event:c.event});
  if(d&&d.ok&&!undo) showToast(`✓ Erinnerung für „${c.title}“ gesetzt`);
  return d;
}

function bindRelSearch(){
  const q=document.getElementById('rel-q'), res=document.getElementById('rel-res');
  const search=async(kind,raw)=>{
    try{
      const d=await fetch('/api/title-search?kind='+kind+'&q='+encodeURIComponent(raw)).then(r=>r.json());
      return (d.candidates||[]).map(x=>({...x,media:kind==='series'?'tv':'movie'}));
    }catch{ return []; }
  };
  const run=()=>{
    const my=++relSearchToken; clearTimeout(relSearchTimer);
    const raw=q.value.trim();
    if(raw.length<2){ res.hidden=true; return; }
    res.innerHTML='<div class="t10-none">Suche…</div>'; res.hidden=false;
    relSearchTimer=setTimeout(async()=>{
      const wantM=relMedia!=='tv', wantT=relMedia!=='movie';
      const [mv,tv]=await Promise.all([wantM?search('movie',raw):[],wantT?search('series',raw):[]]);
      if(my!==relSearchToken) return;
      // Bei "Alle" abwechselnd mischen, damit beide Arten oben auftauchen
      let c=[];
      if(wantM&&wantT){ for(let i=0;i<Math.max(mv.length,tv.length);i++){ if(mv[i]) c.push(mv[i]); if(tv[i]) c.push(tv[i]); } }
      else c=wantM?mv:tv;
      c=c.slice(0,10);
      if(!c.length){ res.innerHTML='<div class="t10-none">Nichts gefunden</div>'; return; }
      const have=new Set(relState.alerts.filter(a=>a.media!=='tv').map(a=>a.tmdb_id));
      res.innerHTML=c.map((x,i)=>`<div class="t10-hit rel-hit" data-i="${i}">
        ${x.poster?`<img src="${x.poster}" alt="">`:'<span class="t10-hit-ph"></span>'}
        <span class="t10-hit-name">${esc(x.title)}<small>${esc(x.year||'')}</small></span>
        <span class="rel-kind ${x.media}">${x.media==='tv'?'Serie':'Film'}</span>
        ${x.media==='movie'&&have.has(x.tmdb_id)?'<span class="rel-set"><i class="fa-solid fa-check"></i> Gesetzt</span>'
          :'<button class="rel-add" type="button"><i class="fa-regular fa-bell"></i> Erinnern</button>'}</div>`).join('');
      res.querySelectorAll('.rel-hit').forEach(h=>h.onmousedown=ev=>ev.preventDefault());   // Fokus behalten
      res.querySelectorAll('.rel-add').forEach(btn=>btn.onclick=async ev=>{
        ev.stopPropagation();
        const x=c[+btn.closest('.rel-hit').dataset.i];
        q.value=''; res.hidden=true;
        if(x.media==='tv') openTvPicker(x); else await relAdd(x);     // Serie: Staffel-Auswahl, Film: direkt
      });
    },350);
  };
  q.oninput=run; q.onfocus=run;
  q.onblur=()=>setTimeout(()=>{res.hidden=true;},150);
}

// ── Serien: Staffel + Start/Finale wählen ─────────────────────────────────────
let relPick=null;      // {cand, info, season, event, loading}

function tmdbLink(id){
  return id?`<a class="rel-lb rel-tmdb" href="https://www.themoviedb.org/tv/${id}" target="_blank" rel="noopener" title="Auf TMDB öffnen" aria-label="Auf TMDB öffnen">TMDB <i class="fa-solid fa-arrow-up-right-from-square"></i></a>`:'';
}

async function openTvPicker(cand){
  relPick={cand,info:null,season:null,event:'start',loading:true};
  renderTvPicker();
  await loadTvPick(null);
}
async function loadTvPick(season){
  relPick.loading=true; renderTvPicker();
  let info=null;
  try{
    info=await fetch(`/api/releases/tv?id=${relPick.cand.tmdb_id}${season?'&season='+season:''}`).then(r=>r.json());
  }catch{}
  if(!relPick) return;
  relPick.loading=false;
  if(!info||!info.ok){ relPick.info={error:true}; renderTvPicker(); return; }
  relPick.info=info;
  relPick.season=info.detail?info.detail.number||info.detail.season:null;
  // Sinnvolle Vorauswahl: läuft die Staffel schon, bleibt nur das Finale; sonst der Start
  const d=info.detail||{};
  relPick.event=(d.state==='running')?'finale':'start';
  renderTvPicker();
}
function closeTvPicker(){
  relPick=null;
  const el=document.getElementById('rel-pick'); if(el) el.remove();
  document.removeEventListener('keydown',tvPickKey);
}
function tvPickKey(ev){ if(ev.key==='Escape') closeTvPicker(); }

function renderTvPicker(){
  let el=document.getElementById('rel-pick');
  if(!relPick){ if(el) el.remove(); return; }
  if(!el){
    el=document.createElement('div'); el.id='rel-pick'; el.className='rel-pick-bg';
    el.onmousedown=ev=>{ if(ev.target===el) closeTvPicker(); };
    document.body.appendChild(el);
    document.addEventListener('keydown',tvPickKey);
  }
  const c=relPick.cand, info=relPick.info, d=info&&info.detail;
  let body;
  if(relPick.loading&&!info){
    body='<div class="rel-sug-note"><span class="spin">↻</span> Lade Staffeln…</div>';
  }else if(!info||info.error){
    body='<div class="rel-sug-note">Die Serie konnte nicht geladen werden.</div>';
  }else{
    const sOpts=info.seasons.map(s=>`<option value="${s.number}"${s.number===relPick.season?' selected':''}>${esc(s.name)}${s.number===info.suggested?' – Vorschlag':''}</option>`).join('');
    const state=d?d.state:'unannounced';
    const startPast=state==='running'||state==='finished';
    const finDone=state==='finished';
    const reasonTxt={'läuft gerade':'läuft gerade','kommt als Nächstes':'kommt als Nächstes','noch nicht angekündigt':'noch nicht angekündigt','zuletzt erschienen':'zuletzt erschienen'}[info.reason]||'';
    const sum=d?[
      d.start?`Start ${relFmtDate(d.start)}`:'Start noch offen',
      d.finale?`Finale ${relFmtDate(d.finale)}`:(d.last_known?`bisher bekannt bis ${relFmtDate(d.last_known)}`:'Finale noch offen'),
      d.episodes?`${d.aired||0} von ${d.episodes} Folgen gelaufen`:''].filter(Boolean).join(' · '):'';
    const opt=(ev,title,dateTxt,note,disabled)=>`
      <label class="pick-opt${relPick.event===ev?' on':''}${disabled?' off':''}">
        <input type="radio" name="tvev" value="${ev}" ${relPick.event===ev?'checked':''} ${disabled?'disabled':''} onchange="relPick.event='${ev}';renderTvPicker()">
        <span class="pick-opt-txt"><b>${title}</b><small>${dateTxt}</small>${note?`<em>${note}</em>`:''}</span>
      </label>`;
    const startDate=d&&d.start?relFmtDate(d.start):'Termin noch offen';
    const finDate=d&&d.finale?relFmtDate(d.finale):'Termin noch offen';
    const canSave=!(finDone)&&!(relPick.event==='start'&&startPast);
    body=`
      <div class="pick-head">
        ${c.poster?`<img src="${c.poster}" alt="">`:'<div class="sug-ph"></div>'}
        <div><h3>${esc(info.title||c.title)}<small>${esc(info.year||'')}</small></h3>
          <span class="rel-chip ${info.status==='Ended'||info.status==='Canceled'?'wait':'soon'}">${esc(info.status==='Returning Series'?'Läuft':info.status==='Ended'?'Beendet':info.status==='Canceled'?'Abgesetzt':info.status)}</span>
        </div>
      </div>
      <label class="pick-lbl" for="pick-season">Staffel${reasonTxt?` <span class="pick-reason">· ${reasonTxt}</span>`:''}</label>
      <select id="pick-season" onchange="loadTvPick(+this.value)">${sOpts}</select>
      <p class="pick-sum">${relPick.loading?'Lade…':esc(sum)}</p>
      <div class="pick-lbl">Wann soll ich dich erinnern?</div>
      <div class="pick-opts">
        ${opt('start','Staffelstart',startDate,startPast?'läuft bereits – Start nicht mehr wählbar':'',startPast)}
        ${opt('finale','Staffelfinale',finDate,finDone?'bereits erschienen':(d&&!d.finale?'Termin kann sich noch ergeben – ich prüfe täglich':''),finDone)}
      </div>`;
  }
  el.innerHTML=`<div class="rel-pick" role="dialog" aria-modal="true" aria-label="Serien-Erinnerung">
    ${body}
    <div class="pick-actions">
      <button class="btn btn-ghost" onclick="closeTvPicker()">Abbrechen</button>
      <button class="btn pick-save" onclick="saveTvAlert()" ${info&&info.ok&&d&&!(d.state==='finished')&&!(relPick.event==='start'&&(d.state==='running'||d.state==='finished'))?'':'disabled'}><i class="fa-regular fa-bell"></i> Erinnerung setzen</button>
    </div>
  </div>`;
}

async function saveTvAlert(){
  if(!relPick||!relPick.info||!relPick.info.ok) return;
  const i=relPick.info, c=relPick.cand;
  const season=relPick.season, event=relPick.event;
  closeTvPicker();
  const d=await relPost('/api/releases/add',{media:'tv',tmdb_id:i.tmdb_id,title:i.title||c.title,year:i.year||c.year,poster:i.poster||c.poster,season,event});
  if(d&&d.ok) showToast(d.duplicate?'Diese Erinnerung gibt es schon':`✓ Erinnerung für „${i.title}“ Staffel ${season} (${event==='finale'?'Finale':'Start'}) gesetzt`);
}

function setRelMedia(m){
  relMedia=m;
  document.querySelectorAll('.rel-media button').forEach(b=>b.classList.toggle('on',b.dataset.m===m));
  const q=document.getElementById('rel-q');
  q.placeholder=m==='tv'?'Serie suchen und Erinnerung setzen…':m==='movie'?'Film suchen und Erinnerung setzen…':'Film oder Serie suchen und Erinnerung setzen…';
  if(q.value.trim().length>=2) q.dispatchEvent(new Event('input')); else document.getElementById('rel-res').hidden=true;
  renderReleases();
}

// ── Start ─────────────────────────────────────────────────────────────────────
(async function relInit(){
  if('serviceWorker' in navigator&&window.isSecureContext){
    try{ await navigator.serviceWorker.register('/sw.js',{scope:'/'}); }catch{}
    navigator.serviceWorker.addEventListener('message',ev=>{
      if(ev.data&&ev.data.type==='open-releases'){ loadReleases(true); switchTab('releases'); }
    });
  }
  await loadReleases(true);
  await relRefreshPushState();
  renderBell();
  if(location.hash==='#releases') switchTab('releases');
  setInterval(()=>loadReleases(false),10*60*1000);
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden) loadReleases(false); });
})();

