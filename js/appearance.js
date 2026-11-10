(function () {
  const key = 'news-overlay-widget.appearance.v1';
  const presets = {
    midnight: {name:'미드나이트', background:'#121720', accent:'#85b7ff'},
    graphite: {name:'차콜', background:'#1b1c20', accent:'#d7ba8c'},
    forest: {name:'포레스트', background:'#10231f', accent:'#6ed6ae'},
    light: {name:'라이트', background:'#f4f6fb', accent:'#315fce'},
  };
  const defaults = {version:1, preset:'midnight', background:presets.midnight.background, accent:presets.midnight.accent};
  const root = document.documentElement;
  const dialog = document.getElementById('appearance-settings');
  const open = document.getElementById('settings-open');
  const status = document.getElementById('settings-save-status');
  const background = document.getElementById('theme-background');
  const accent = document.getElementById('theme-accent');
  const choices = Array.from(document.querySelectorAll('.theme-preset'));

  function restore(value) {
    try {
      const saved = JSON.parse(value);
      if (saved?.version !== 1 || ![saved.background, saved.accent].every(x => typeof x === 'string' && /^#[0-9a-f]{6}$/i.test(x))) return {...defaults};
      const colors = {background:saved.background.toLowerCase(), accent:saved.accent.toLowerCase()};
      const preset = Object.keys(presets).find(id => presets[id].background === colors.background && presets[id].accent === colors.accent) || 'custom';
      return {version:1, preset, ...colors};
    } catch { return {...defaults}; }
  }

  function load() {
    try { return restore(window.localStorage.getItem(key)); }
    catch { return {...defaults}; }
  }

  const rgb = hex => [1,3,5].map(i => parseInt(hex.slice(i,i+2),16));
  function mix(a, b, amount) {
    const other = rgb(b);
    return '#' + rgb(a).map((v,i) => Math.round(v + (other[i]-v)*amount).toString(16).padStart(2,'0')).join('');
  }
  function luminance(hex) {
    return rgb(hex).map(v => {v/=255; return v<=0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4;})
      .reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
  }
  function contrast(a,b) {
    const x=luminance(a), y=luminance(b);
    return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);
  }
  function readable(color, surfaces, minimum=4.5) {
    const floor = value => Math.min(...surfaces.map(bg=>contrast(value,bg)));
    if (floor(color)>=minimum) return color;
    const end = floor('#ffffff')>floor('#000000') ? '#ffffff' : '#000000';
    for(let step=1;step<=20;step++) {
      const value=mix(color,end,step/20);
      if(floor(value)>=minimum) return value;
    }
    return end;
  }

  function palette(settings) {
    const bg=settings.background, dark=luminance(bg)<0.179;
    const ink=dark?'#ffffff':'#000000', edge=dark?'#000000':'#ffffff';
    // Keep derived surfaces on the same side of the contrast boundary.
    function surface(candidate) {
      for(let step=0;step<=20;step++) {
        const value=mix(candidate,edge,step/20);
        if(contrast(ink,value)>=5) return value;
      }
      return edge;
    }
    const card=surface(mix(bg,'#ffffff',dark?0.035:0.6));
    const inset=surface(mix(bg,edge,dark?0.23:0.45));
    const selected=surface(mix(bg,settings.accent,dark?0.17:0.12));
    const hover=surface(mix(bg,settings.accent,dark?0.11:0.06));
    const surfaces=[bg,card,inset,selected,hover];
    const text=readable(dark?'#eef2f8':'#172033',surfaces);
    const sub=readable(mix(text,bg,0.27),surfaces);
    const dim=readable(mix(text,bg,0.38),surfaces);
    const point=readable(settings.accent,surfaces);
    const vars={canvas:mix(bg,edge,dark?0.35:0.04),surface:bg,'surface-2':card,line:mix(bg,ink,dark?0.16:0.2),
      'text-main':text,'text-sub':sub,'text-dim':dim,accent:point,inset,control:card,selected,
      'selected-text':readable(settings.accent,[selected]),'selected-border':mix(bg,point,0.5),
      card,'card-top':selected,'card-hover':hover,'strong-line':mix(bg,point,0.5),
      'source-text':readable(mix(text,point,0.4),surfaces),'scroll-thumb':mix(bg,ink,0.3),
      'skeleton':mix(bg,ink,0.08),'brand-soft':mix(point,ink,0.3)};
    for(const [name,color] of Object.entries({ok:'#31b77f',warn:'#b57d22',error:'#e25565',positive:'#32b980',negative:'#e25565',mixed:'#c29336',fresh:'#528bd1',type:'#8895b0',breaking:'#e76f89',important:'#c59942',economic:'#43aa91',earnings:'#8c80d9',pin:'#bf9b40'})) {
      const badge=surface(mix(bg,color,dark?0.18:0.12));
      vars[name]=readable(color,surfaces);
      vars[`${name}-bg`]=badge;
      vars[`${name}-text`]=readable(color,[badge]);
    }
    vars['pin-border']=mix(bg,vars.pin,0.5);
    return {vars,dark};
  }

  let current=load();
  function apply() {
    const {vars,dark}=palette(current);
    for(const [name,value] of Object.entries(vars)) root.style.setProperty(`--${name}`,value);
    root.style.colorScheme=dark?'dark':'light';
    root.dataset.theme=current.preset;
    background.value=current.background;
    accent.value=current.accent;
    document.getElementById('theme-background-value').textContent=current.background.toUpperCase();
    document.getElementById('theme-accent-value').textContent=current.accent.toUpperCase();
    document.getElementById('settings-theme-name').textContent=presets[current.preset]?.name || '직접 설정';
    choices.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.theme===current.preset)));
  }
  function save() {
    try {
      window.localStorage.setItem(key,JSON.stringify(current));
      status.textContent='저장됨 · 같은 브라우저의 위젯과 패널에 적용됩니다.';
    } catch { status.textContent='이 창에 적용됨 · 브라우저에서 설정 저장을 허용하지 않습니다.'; }
  }
  function choose(value) {current=restore(JSON.stringify(value));apply();save();}
  choices.forEach(button=>button.addEventListener('click',()=>choose({version:1,...presets[button.dataset.theme]})));
  for(const [name,input] of [['background',background],['accent',accent]]) {
    input.addEventListener('input',()=>{
      current=restore(JSON.stringify({...current,[name]:input.value}));
      apply();
      status.textContent='미리보기 · 색상 선택을 마치면 저장됩니다.';
    });
    input.addEventListener('change',()=>choose({...current,[name]:input.value}));
  }
  open.addEventListener('click',()=>dialog.showModal());
  function close() {dialog.close();}
  document.getElementById('settings-close').addEventListener('click',close);
  document.getElementById('settings-done').addEventListener('click',close);
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab') return;
    const controls=Array.from(dialog.querySelectorAll('button:not(:disabled), input:not(:disabled)'));
    const first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey && document.activeElement===first) {event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last) {event.preventDefault();first.focus();}
  });
  // Persist a preview when the picker/dialog is closed with keyboard controls too.
  dialog.addEventListener('close',()=>{save();open.focus();});
  document.getElementById('theme-reset').addEventListener('click',()=>choose(defaults));
  window.addEventListener('storage',event=>{
    if(event.key!==key && event.key!==null) return;
    current=restore(event.newValue);
    apply();
    status.textContent='다른 창에서 변경한 테마를 적용했습니다.';
  });
  apply();
})();
