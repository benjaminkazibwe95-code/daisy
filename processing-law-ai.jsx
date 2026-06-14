import { useState, useRef, useEffect } from "react";

// ============================================================
// LAW 1 — T ORDER
// ============================================================
const T_ORDER = {};
for (let i = 0; i < 26; i++) T_ORDER[String.fromCharCode(65 + i)] = i + 1;
function wordValue(w) { return w.toUpperCase().split("").filter(c => T_ORDER[c]).reduce((a, b) => a + T_ORDER[b], 0); }

// ============================================================
// LAW 2 — RICH DICTIONARY (multi-field entries)
// ============================================================
const DICTIONARY = {
  water: {
    definition: "a clear liquid compound of hydrogen and oxygen",
    what_it_does: "sustains all known life on Earth",
    without_it: "no life can survive",
    examples: "oceans, rivers, rain, drinking water",
    related: ["liquid","hydrogen","oxygen","life"],
  },
  gravity: {
    definition: "the force that attracts all objects with mass toward each other",
    what_it_does: "keeps everything grounded on Earth and holds planets in orbit",
    without_it: "all objects, air, water and living things would drift into space",
    examples: "an apple falling, the moon orbiting Earth, tides",
    related: ["force","mass","orbit","weight","acceleration"],
  },
  fire: {
    definition: "rapid chemical oxidation that releases heat and light",
    what_it_does: "produces energy, heat and light through burning",
    without_it: "combustion and many chemical reactions would not exist",
    examples: "candle flame, forest fire, burning wood",
    related: ["heat","oxygen","combustion","energy"],
  },
  sun: {
    definition: "the star at the center of our solar system",
    what_it_does: "provides light and heat that sustains life on Earth",
    without_it: "Earth would freeze and all life would end",
    examples: "sunrise, solar energy, sunlight",
    related: ["star","light","heat","solar","energy"],
  },
  electricity: {
    definition: "the flow of electric charge through a conductor",
    what_it_does: "powers machines, lights, communication and modern life",
    without_it: "all modern technology would stop functioning",
    examples: "lightning, power grids, batteries, circuits",
    related: ["current","voltage","power","conductor","energy"],
  },
  photosynthesis: {
    definition: "the process by which plants convert sunlight, water and CO2 into food",
    what_it_does: "produces oxygen and glucose that sustain life on Earth",
    without_it: "plants would die and oxygen levels would collapse",
    examples: "leaves absorbing sunlight, crops growing, algae in oceans",
    related: ["sunlight","water","oxygen","plants","glucose"],
  },
  energy: {
    definition: "the capacity to do work or cause change in a system",
    what_it_does: "powers all movement, heat, light and chemical reactions",
    without_it: "nothing in the universe could move or change",
    examples: "electricity, heat, light, motion, food",
    related: ["power","force","heat","light","motion"],
  },
  atom: {
    definition: "the smallest unit of a chemical element that retains its properties",
    what_it_does: "forms all matter in the universe by combining into molecules",
    without_it: "no matter or physical substances would exist",
    examples: "hydrogen atom, carbon atom, oxygen atom",
    related: ["proton","neutron","electron","molecule","element"],
  },
  evolution: {
    definition: "the process by which species change over generations through natural selection",
    what_it_does: "explains the diversity of all life on Earth",
    without_it: "all species would remain unchanged and many would go extinct",
    examples: "humans evolving from primates, birds from dinosaurs, antibiotic resistance",
    related: ["species","natural selection","genes","adaptation","biology"],
  },
  dna: {
    definition: "the molecule that carries genetic instructions for all living organisms",
    what_it_does: "determines traits, guides development and enables inheritance",
    without_it: "life could not reproduce or pass on characteristics",
    examples: "eye color genes, inherited diseases, forensic DNA testing",
    related: ["genes","chromosome","protein","cell","heredity"],
  },
  oxygen: {
    definition: "a chemical element essential for respiration and combustion",
    what_it_does: "enables breathing in animals and burning in fire",
    without_it: "animals cannot breathe and fire cannot burn",
    examples: "air we breathe, medical oxygen, combustion",
    related: ["air","breathing","fire","water","atmosphere"],
  },
  brain: {
    definition: "the organ that controls all body functions and processes thought",
    what_it_does: "processes information, controls movement and generates consciousness",
    without_it: "no thought, movement or bodily function could occur",
    examples: "thinking, memory, dreaming, controlling heartbeat",
    related: ["neuron","memory","thought","nervous system","intelligence"],
  },
  internet: {
    definition: "a global network of interconnected computers sharing information",
    what_it_does: "connects billions of people and enables communication worldwide",
    without_it: "global communication, commerce and information sharing would collapse",
    examples: "websites, email, social media, online shopping",
    related: ["network","computer","data","communication","technology"],
  },
  virus: {
    definition: "a microscopic infectious agent that replicates inside living cells",
    what_it_does: "hijacks host cells to reproduce, often causing disease",
    without_it: "many diseases like flu, HIV and COVID would not exist",
    examples: "influenza, HIV, COVID-19, common cold",
    related: ["bacteria","disease","immune system","cell","infection"],
  },
  democracy: {
    definition: "a system of government where citizens elect their leaders",
    what_it_does: "gives people power over their government through voting",
    without_it: "citizens have no say in how they are governed",
    examples: "elections, parliament, voting, Uganda's government",
    related: ["government","election","freedom","constitution","rights"],
  },
  ai: {
    definition: "the simulation of human intelligence by machines and computer systems",
    what_it_does: "processes information, learns patterns and solves complex problems",
    without_it: "machines could only follow fixed instructions with no adaptability",
    examples: "ChatGPT, facial recognition, self-driving cars, recommendation systems",
    related: ["machine learning","computer","data","algorithm","technology"],
  },
  mathematics: {
    definition: "the science of numbers, quantities, shapes and their relationships",
    what_it_does: "provides tools for solving problems in science, engineering and life",
    without_it: "science, engineering, finance and technology would not function",
    examples: "algebra, geometry, calculus, statistics",
    related: ["numbers","equations","geometry","algebra","calculation"],
  },
  climate: {
    definition: "the long-term pattern of weather in a region over many years",
    what_it_does: "determines what plants grow, how people live and weather patterns",
    without_it: "ecosystems would have no stable patterns to adapt to",
    examples: "tropical climate in Uganda, Arctic cold, desert heat",
    related: ["weather","temperature","rainfall","environment","seasons"],
  },
  education: {
    definition: "the process of acquiring knowledge and skills through learning and teaching",
    what_it_does: "develops individuals and enables societies to progress",
    without_it: "knowledge cannot be passed between generations",
    examples: "schools, universities, reading, training",
    related: ["knowledge","school","learning","skills","teacher"],
  },
  money: {
    definition: "a medium of exchange used in transactions for goods and services",
    what_it_does: "enables trade, saving and economic activity between people",
    without_it: "people would have to barter goods directly",
    examples: "Uganda shilling, US dollar, digital payments",
    related: ["economy","trade","bank","wealth","finance"],
  },
};

// ============================================================
// JOINER DICTIONARY — How words connect sentences
// ============================================================
const JOINERS = {
  // Conditional
  if: { type: "conditional", frame: "if [X], then [result]" },
  when: { type: "time_condition", frame: "when [X] happens, [result]" },
  unless: { type: "exception", frame: "[result] unless [X]" },
  // Cause
  because: { type: "cause", frame: "[result] because [X]" },
  since: { type: "cause", frame: "since [X], [result]" },
  due: { type: "cause", frame: "due to [X], [result]" },
  // Result
  therefore: { type: "result", frame: "[X], therefore [result]" },
  thus: { type: "result", frame: "[X], thus [result]" },
  would: { type: "outcome", frame: "if [X], [outcome] would occur" },
  // Contrast
  but: { type: "contrast", frame: "[X], but [contrast]" },
  however: { type: "contrast", frame: "[X]. However, [contrast]" },
  although: { type: "contrast", frame: "although [X], [contrast]" },
  // Addition
  also: { type: "addition", frame: "[X]. It also [addition]" },
  and: { type: "addition", frame: "[X] and [addition]" },
  additionally: { type: "addition", frame: "[X]. Additionally, [addition]" },
  // Comparison
  than: { type: "comparison", frame: "[X] more than [comparison]" },
  like: { type: "similarity", frame: "[X] works like [comparison]" },
  // Purpose
  to: { type: "purpose", frame: "[X] in order to [purpose]" },
  for: { type: "purpose", frame: "[X] for the purpose of [purpose]" },
};

// ============================================================
// LAW 3 — OPERATOR LAW
// ============================================================
const OPERATORS = {
  what: "DEFINE", who: "PERSON", where: "LOCATION",
  when: "TIME", how: "PROCESS", why: "REASON",
  explain: "EXPLAIN", describe: "DESCRIBE",
  compare: "COMPARE", define: "DEFINE",
  calculate: "MATH", solve: "MATH", compute: "MATH",
  would: "HYPOTHETICAL", could: "HYPOTHETICAL",
  should: "ADVICE", tell: "EXPLAIN",
};

function detectOperator(words) {
  for (const w of words) {
    const op = OPERATORS[w.toLowerCase()];
    if (op) return { word: w.toLowerCase(), action: op };
  }
  return { word: "unknown", action: "GENERAL" };
}

// ============================================================
// LAW 4 — MATH ENGINE
// ============================================================
function tryMath(q) {
  const clean = q.toLowerCase()
    .replace(/what is|calculate|solve|compute|find|equals/gi, "")
    .replace(/×/g,"*").replace(/÷/g,"/")
    .replace(/squared/g,"**2").replace(/cubed/g,"**3")
    .replace(/square root of\s*/g,"Math.sqrt(")
    .replace(/\bpi\b/g,"Math.PI")
    .replace(/(\d+\.?\d*)\s*%\s*of\s*(\d+\.?\d*)/g,"($1/100)*$2")
    .trim();
  if (!/[\d]/.test(clean)) return null;
  if (!/[\+\-\*\/\^%]/.test(clean) && !/Math\./.test(clean)) return null;
  try {
    // eslint-disable-next-line no-new-func
    const r = Function('"use strict"; return (' + clean + ')')();
    if (typeof r === "number" && isFinite(r))
      return Number.isInteger(r) ? r.toString() : r.toFixed(6).replace(/\.?0+$/,"");
  } catch {}
  return null;
}

function tryScenarioMath(q) {
  const text = q.toLowerCase();
  const wordNums = {one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,eleven:11,twelve:12,fifteen:15,twenty:20,thirty:30,fifty:50,hundred:100};
  let processed = text;
  for (const [w, v] of Object.entries(wordNums)) processed = processed.replace(new RegExp(`\\b${w}\\b`,'g'),` ${v} `);
  const nums = processed.match(/\d+(\.\d+)?/g);
  if (!nums || nums.length < 2) return null;
  const n = nums.map(Number);
  if (/left|remain|after giving|after spending|fewer|less/i.test(text)) return (n[0]-n[1]).toString();
  if (/total|altogether|combined|together|more|added/i.test(text)) return (n[0]+n[1]).toString();
  if (/each|per|every|split|shared|divided/i.test(text)) return (n[0]/n[1]).toFixed(2).replace(/\.?0+$/,"");
  if (/times|multiplied|product/i.test(text)) return (n[0]*n[1]).toString();
  if (/distance|speed.*time|how far/i.test(text) && n.length>=2) return (n[0]*n[1]).toString();
  return null;
}

// ============================================================
// LAW 7 — EMOTION ENGINE
// ============================================================
const EMOTIONS = {
  sad:{r:"comfort",c:"#6C8EBF"},hurt:{r:"comfort",c:"#6C8EBF"},lonely:{r:"connect",c:"#6C8EBF"},
  depressed:{r:"comfort",c:"#6C8EBF"},broken:{r:"comfort",c:"#6C8EBF"},
  happy:{r:"celebrate",c:"#92FE9D"},excited:{r:"celebrate",c:"#92FE9D"},proud:{r:"celebrate",c:"#92FE9D"},
  angry:{r:"calm",c:"#FF6B6B"},frustrated:{r:"calm",c:"#FF6B6B"},mad:{r:"calm",c:"#FF6B6B"},
  scared:{r:"reassure",c:"#F0A500"},worried:{r:"reassure",c:"#F0A500"},anxious:{r:"reassure",c:"#F0A500"},
  confused:{r:"clarify",c:"#C471ED"},lost:{r:"guide",c:"#C471ED"},
  motivated:{r:"build",c:"#FFC300"},inspired:{r:"build",c:"#FFC300"},
};
const EMOTION_REPLIES = {
  comfort:["I hear you. That weight is real — you don't have to carry it alone.","What you feel is valid. Take it one breath at a time."],
  celebrate:["That energy is real — hold it and build on it.","Yes! Channel that into something great."],
  calm:["That frustration makes sense. Let's work through it clearly.","Take a breath. The anger is valid. Now let's think."],
  reassure:["That fear means you care. You've got this.","Most of what we worry about never happens. Focus on what you can control."],
  connect:["You're not alone. I'm right here.","That loneliness won't last — it never does."],
  guide:["Let's find direction together. What matters most right now?","Feeling lost means you're between chapters. The next one is coming."],
  clarify:["Let's break it down step by step until it clicks.","Good question. Let me make this clear."],
  build:["That fire is real. Now let's turn it into a plan.","Dreams need structure. Let's build the roadmap."],
};
function detectEmotion(text) {
  const words = text.toLowerCase().split(/\s+/);
  for (const w of words) {
    const clean = w.replace(/[^a-z]/g,"");
    if (EMOTIONS[clean]) return EMOTIONS[clean];
  }
  if (/i (feel|am|m) (sad|down|bad|terrible)/i.test(text)) return EMOTIONS["sad"];
  if (/i (feel|am|m) (happy|great|amazing)/i.test(text)) return EMOTIONS["happy"];
  if (/i (don.t|dont) understand/i.test(text)) return EMOTIONS["confused"];
  return null;
}
function emotionReply(type) {
  const list = EMOTION_REPLIES[type]||EMOTION_REPLIES["clarify"];
  return list[Math.floor(Math.random()*list.length)];
}

// ============================================================
// SYNTHESIS ENGINE — The Core New System
// ============================================================
function extractWords(question) {
  return question.toLowerCase().replace(/[?!.,]/g,"").split(/\s+/).filter(w => w.length > 1);
}

function detectJoiners(words) {
  return words.filter(w => JOINERS[w]).map(w => ({ word: w, ...JOINERS[w] }));
}

function collectDictionaryData(words, fullDict) {
  const found = [];
  for (const w of words) {
    if (fullDict[w]) found.push({ word: w, data: fullDict[w] });
  }
  return found;
}

function synthesizeAnswer(question, operator, collected, joiners) {
  if (collected.length === 0) return null;

  const primary = collected[0];
  const secondary = collected[1];
  const action = operator.action;

  // Frame based on operator
  let response = "";

  if (action === "DEFINE" || action === "EXPLAIN" || action === "DESCRIBE") {
    response = `${capitalize(primary.word)} is ${primary.data.definition}.`;
    if (primary.data.what_it_does) response += ` It ${primary.data.what_it_does}.`;
    if (primary.data.examples) response += ` Examples include ${primary.data.examples}.`;
    if (secondary) response += ` It is closely related to ${secondary.word}, which is ${secondary.data.definition}.`;
  }

  else if (action === "PROCESS") {
    response = `${capitalize(primary.word)} works through ${primary.data.definition}.`;
    if (primary.data.what_it_does) response += ` Specifically, it ${primary.data.what_it_does}.`;
    if (primary.data.examples) response += ` This can be seen in ${primary.data.examples}.`;
  }

  else if (action === "REASON") {
    response = `${capitalize(primary.word)} is important because it ${primary.data.what_it_does || primary.data.definition}.`;
    if (primary.data.without_it) response += ` Without it, ${primary.data.without_it}.`;
  }

  else if (action === "HYPOTHETICAL") {
    // "If gravity disappeared what would happen"
    const subject = collected.find(c => c.data.without_it);
    if (subject) {
      response = `If ${subject.word} — ${subject.data.definition} — were to disappear, ${subject.data.without_it}.`;
      if (subject.data.what_it_does) response += ` This is because ${subject.word} currently ${subject.data.what_it_does}.`;
    } else {
      response = `Without ${primary.word}, ${primary.data.without_it || "significant consequences would follow"}.`;
    }
  }

  else if (action === "COMPARE") {
    if (secondary) {
      response = `${capitalize(primary.word)} is ${primary.data.definition}, while ${secondary.word} is ${secondary.data.definition}.`;
      response += ` ${capitalize(primary.word)} ${primary.data.what_it_does || ""}, whereas ${secondary.word} ${secondary.data.what_it_does || ""}.`;
    } else {
      response = `${capitalize(primary.word)} is ${primary.data.definition}.`;
    }
  }

  else if (action === "GENERAL") {
    // Multi-concept scenario — join all collected
    if (collected.length === 1) {
      response = `${capitalize(primary.word)} is ${primary.data.definition}. It ${primary.data.what_it_does || ""}.`;
    } else {
      response = collected.map(c => `${capitalize(c.word)} is ${c.data.definition}`).join(". ") + ".";
    }
  }

  // Apply joiner logic if detected
  if (joiners.length > 0) {
    const joiner = joiners[0];
    if (joiner.type === "cause" && secondary) {
      response = `${capitalize(primary.word)} ${primary.data.what_it_does} because ${secondary.word} ${secondary.data.definition}.`;
    }
    if (joiner.type === "contrast" && secondary) {
      response += ` However, ${secondary.word} ${secondary.data.what_it_does || "works differently"}.`;
    }
  }

  return response || null;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ============================================================
// LAW 6 — AI FALLBACK (for what pure laws can't handle)
// ============================================================
async function fallbackAI(question, history) {
  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({
        model:"claude-sonnet-4-6", max_tokens:1000,
        system:"You are a precise AI. Answer in 2-3 clear sentences. Direct and factual. Then: KEY: <keyword> | DEFINITION: <one sentence>",
        messages:[
          ...history.slice(-6).map(m=>({role:m.role,content:m.content})),
          {role:"user",content:question}
        ]
      })
    });
    const data = await res.json();
    const text = data.content?.[0]?.text||"";
    const lines = text.split("\n").filter(Boolean);
    const keyLine = lines.find(l=>l.startsWith("KEY:"));
    const answer = lines.filter(l=>!l.startsWith("KEY:")).join(" ").trim();
    let newEntry = null;
    if (keyLine) {
      const m = keyLine.match(/KEY:\s*(\w+)\s*\|\s*DEFINITION:\s*(.+)/);
      if (m) newEntry = {key:m[1].toLowerCase(), definition:m[2].trim()};
    }
    return {answer, newEntry, offline:false};
  } catch { return {answer:null, newEntry:null, offline:true}; }
}

// ============================================================
// STORAGE
// ============================================================
async function loadLearned() {
  try { const r = await window.storage.get("dict-v5"); return r?JSON.parse(r.value):{}; } catch { return {}; }
}
async function saveLearned(d) {
  try { await window.storage.set("dict-v5", JSON.stringify(d)); } catch {}
}

// ============================================================
// MASTER ENGINE — ALL 7 LAWS + SYNTHESIS
// ============================================================
async function processQuery(question, learnedDict, history, onNewWord) {
  const fullDict = {...DICTIONARY, ...learnedDict};
  const words = extractWords(question);
  const operator = detectOperator(words);
  const joiners = detectJoiners(words);
  const collected = collectDictionaryData(words, fullDict);
  const emotion = detectEmotion(question);

  // LAW 4 — Math always first
  const math = tryMath(question);
  if (math) return {answer: math, source:"math"};

  const scenario = tryScenarioMath(question);
  if (scenario) return {answer: scenario, source:"scenario"};

  // SYNTHESIS ENGINE — Always tries before anything else
  // Even one word in dictionary = try to build an answer
  if (collected.length > 0) {
    const synthesized = synthesizeAnswer(question, operator, collected, joiners);
    if (synthesized) {
      const prefix = emotion ? emotionReply(emotion.r) + " — " : "";
      return {
        answer: prefix + synthesized,
        source: collected.length > 1 ? "synthesis" : "dictionary",
        emotionColor: emotion?.c || null
      };
    }
  }

  // LAW 7 — Pure emotion, no facts needed
  if (emotion && collected.length === 0) {
    return {answer: emotionReply(emotion.r), source:"emotion", emotionColor: emotion.c};
  }

  // LAW 6 — ONLY fires when truly zero dictionary match
  // This is for words that don't exist in dictionary yet
  const {answer, newEntry, offline} = await fallbackAI(question, history);
  if (offline||!answer) {
    return {
      answer: "I don't have that in my dictionary yet. I'm growing every day — ask me something I know or add it to my dictionary.",
      source:"offline"
    };
  }
  if (newEntry) {
    const updated = {
      ...learnedDict,
      [newEntry.key]: {
        definition: newEntry.definition,
        what_it_does: "",
        without_it: "",
        examples: "",
        related: [],
      }
    };
    await saveLearned(updated);
    onNewWord(updated, newEntry.key);
  }
  return {answer, source:"external", learned:newEntry};
}

// ============================================================
// UI
// ============================================================
const SOURCE_META = {
  math:        {color:"#FFC300", icon:"⚡", label:"Math Engine · Law 4"},
  scenario:    {color:"#FFC300", icon:"📐", label:"Scenario Math · Law 4"},
  dictionary:  {color:"#C471ED", icon:"📖", label:"Dictionary · Law 5"},
  synthesis:   {color:"#00C9FF", icon:"🧬", label:"Synthesis Engine · Laws 1-5"},
  learned:     {color:"#92FE9D", icon:"🧠", label:"Learned Memory · Law 6"},
  external:    {color:"#F64F59", icon:"🔍", label:"AI Fallback · Law 6"},
  emotion:     {color:"#FF6B9D", icon:"❤️", label:"Emotion Engine · Law 7"},
  offline:     {color:"#666",    icon:"📴", label:"Offline"},
};

export default function App() {
  const [messages, setMessages] = useState([{
    role:"assistant",
    content:"All 7 laws active with Synthesis Engine. Ask me facts, math, scenarios or talk to me.",
    source:"system"
  }]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [learnedDict, setLearnedDict] = useState({});
  const [tab, setTab] = useState("chat");
  const [learned, setLearned] = useState(0);
  const bottomRef = useRef();
  const inputRef = useRef();

  useEffect(() => { loadLearned().then(d => { setLearnedDict(d); setLearned(Object.keys(d).length); }); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({behavior:"smooth"}); }, [messages]);

  async function send() {
    if (!input.trim()||loading) return;
    const msg = input.trim();
    setInput("");
    const updated = [...messages, {role:"user",content:msg}];
    setMessages(updated);
    setLoading(true);
    const history = updated.filter(m=>m.role!=="system"&&m.source!=="system");
    const result = await processQuery(msg, learnedDict, history, (d, key) => {
      setLearnedDict(d);
      setLearned(Object.keys(d).length);
    });
    setMessages(prev => [...prev, {role:"assistant", ...result}]);
    setLoading(false);
  }

  const totalWords = Object.keys(DICTIONARY).length + learned;
  const tests = [
    "What is gravity?",
    "If gravity disappeared what would happen?",
    "How does photosynthesis work?",
    "Why is water important?",
    "Compare water and fire",
    "Calculate 25 * 48",
    "John has 20 mangoes gives 7 to Sarah how many left?",
    "What is electricity and why does it matter?",
    "I feel confused about science",
    "Explain DNA",
  ];

  return (
    <div style={{height:"100vh",display:"flex",flexDirection:"column",background:"#080810",color:"#E8E8F0",fontFamily:"'Courier New', monospace"}}>
      {/* Header */}
      <div style={{padding:"10px 16px",borderBottom:"1px solid #111",flexShrink:0}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",maxWidth:720,margin:"0 auto"}}>
          <div>
            <div style={{fontSize:8,color:"#00C9FF",letterSpacing:5}}>THE PROCESSING LAW</div>
            <div style={{fontSize:16,fontWeight:900,background:"linear-gradient(90deg,#00C9FF,#92FE9D,#FFC300,#FF6B9D)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>
              AI ENGINE v4.0 — SYNTHESIS
            </div>
          </div>
          <div style={{display:"flex",gap:12}}>
            {[{v:totalWords,l:"WORDS",c:"#92FE9D"},{v:learned,l:"LEARNED",c:"#F64F59"},{v:7,l:"LAWS",c:"#FF6B9D"}].map(({v,l,c})=>(
              <div key={l} style={{textAlign:"center"}}>
                <div style={{fontSize:16,fontWeight:900,color:c}}>{v}</div>
                <div style={{fontSize:7,color:"#333",letterSpacing:1}}>{l}</div>
              </div>
            ))}
          </div>
        </div>
        <div style={{display:"flex",gap:4,maxWidth:720,margin:"8px auto 0"}}>
          {["chat","test","laws","memory"].map(t=>(
            <button key={t} onClick={()=>setTab(t)} style={{flex:1,background:tab===t?"#10101A":"transparent",border:`1px solid ${tab===t?"#00C9FF33":"#1A1A1A"}`,borderRadius:6,padding:"5px",color:tab===t?"#00C9FF":"#333",fontSize:9,cursor:"pointer",fontFamily:"'Courier New', monospace",letterSpacing:2,textTransform:"uppercase"}}>
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* CHAT */}
      {tab==="chat" && <>
        <div style={{flex:1,overflowY:"auto",padding:16,maxWidth:720,width:"100%",margin:"0 auto",boxSizing:"border-box"}}>
          {messages.map((msg,i)=>{
            const meta = SOURCE_META[msg.source]||{};
            const isUser = msg.role==="user";
            return (
              <div key={i} style={{marginBottom:14,display:"flex",flexDirection:isUser?"row-reverse":"row",gap:10,animation:"fadeIn 0.3s ease"}}>
                <div style={{width:32,height:32,borderRadius:"50%",background:isUser?"#1A1A2E":"#10101A",border:`1px solid ${isUser?"#333":(meta.color||"#333")}44`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:13,flexShrink:0,marginTop:2}}>
                  {isUser?"👤":"🤖"}
                </div>
                <div style={{maxWidth:"82%"}}>
                  {!isUser&&msg.source&&msg.source!=="system"&&(
                    <div style={{fontSize:8,color:msg.emotionColor||meta.color||"#555",letterSpacing:2,marginBottom:4}}>
                      {meta.icon} {meta.label}
                      {msg.learned&&<span style={{color:"#FFC300",marginLeft:8}}>+ SAVED "{msg.learned.key}"</span>}
                    </div>
                  )}
                  <div style={{
                    background:isUser?"#1A1A2E":"#10101A",
                    border:`1px solid ${isUser?"#2A2A3E":(msg.emotionColor||meta.color||"#1A1A2E")}33`,
                    borderRadius:isUser?"12px 12px 4px 12px":"12px 12px 12px 4px",
                    padding:"12px 16px",
                    fontSize:["math","scenario"].includes(msg.source)?22:13,
                    fontWeight:["math","scenario"].includes(msg.source)?900:400,
                    color:["math","scenario"].includes(msg.source)?"#FFC300":"#D0D0E0",
                    lineHeight:1.8,whiteSpace:"pre-wrap"
                  }}>
                    {msg.content}
                  </div>
                </div>
              </div>
            );
          })}
          {loading&&(
            <div style={{display:"flex",gap:10,marginBottom:14}}>
              <div style={{width:32,height:32,borderRadius:"50%",background:"#10101A",border:"1px solid #333",display:"flex",alignItems:"center",justifyContent:"center",fontSize:13}}>🤖</div>
              <div style={{background:"#10101A",border:"1px solid #1A1A2E",borderRadius:"12px 12px 12px 4px",padding:"12px 20px",fontSize:13,color:"#444"}}>
                Processing<span style={{color:"#00C9FF",animation:"blink 1s infinite"}}> ···</span>
              </div>
            </div>
          )}
          <div ref={bottomRef}/>
        </div>
        <div style={{padding:"10px 16px",borderTop:"1px solid #111",background:"#080810",flexShrink:0}}>
          <div style={{maxWidth:720,margin:"0 auto",display:"flex",gap:8}}>
            <input ref={inputRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&send()}
              placeholder="Ask anything — facts, math, scenarios, emotions..."
              style={{flex:1,background:"#10101A",border:"1px solid #1A1A2E",borderRadius:24,padding:"10px 18px",color:"#E8E8F0",fontSize:13,fontFamily:"'Courier New', monospace",outline:"none"}}/>
            <button onClick={send} disabled={loading} style={{background:loading?"#10101A":"linear-gradient(135deg,#00C9FF,#92FE9D)",border:"none",borderRadius:"50%",width:42,height:42,color:"#080810",fontWeight:900,fontSize:16,cursor:loading?"not-allowed":"pointer"}}>➤</button>
          </div>
        </div>
      </>}

      {/* TEST TAB */}
      {tab==="test"&&(
        <div style={{flex:1,overflowY:"auto",padding:16,maxWidth:720,margin:"0 auto",width:"100%",boxSizing:"border-box"}}>
          <div style={{fontSize:9,color:"#555",letterSpacing:3,marginBottom:14}}>TEST SUITE — Click to test each scenario</div>
          {tests.map((t,i)=>(
            <button key={i} onClick={()=>{setTab("chat");setInput(t);setTimeout(()=>inputRef.current?.focus(),100);}}
              style={{display:"block",width:"100%",background:"#10101A",border:"1px solid #1A1A2E",borderRadius:8,padding:"12px 16px",color:"#888",fontSize:12,cursor:"pointer",fontFamily:"'Courier New', monospace",textAlign:"left",marginBottom:8}}>
              <span style={{color:"#333",marginRight:8}}>{i+1}.</span> {t}
            </button>
          ))}
        </div>
      )}

      {/* LAWS TAB */}
      {tab==="laws"&&(
        <div style={{flex:1,overflowY:"auto",padding:16,maxWidth:720,margin:"0 auto",width:"100%",boxSizing:"border-box"}}>
          {[
            {n:1,name:"T ORDER",desc:"Every letter = a fixed number. A=1...Z=26. The encoding base."},
            {n:2,name:"RICH DICTIONARY",desc:"Every word has multi-field entries: definition, what it does, without it, examples, related words. Grows forever."},
            {n:3,name:"OPERATOR LAW",desc:"Question words are operators. WHAT, HOW, WHY, IF, COMPARE each shape the answer differently."},
            {n:4,name:"MATH ENGINE",desc:"Direct equations and word problem scenarios. Extracts numbers and operations from context. Unbeatable on math."},
            {n:5,name:"SYNTHESIS ENGINE",desc:"Breaks the question word by word. Collects all dictionary data. Detects joiners (if, because, would, however). Frames a full natural answer."},
            {n:6,name:"FALLBACK + SAVE",desc:"For what pure laws can't handle yet — researches, frames, saves permanently. Dictionary grows with every use."},
            {n:7,name:"EMOTION LAW",desc:"Detects emotional tone. Maps to response mode. Can combine emotion with knowledge in one answer."},
          ].map(({n,name,desc})=>(
            <div key={n} style={{background:"#10101A",borderLeft:`4px solid ${["#00C9FF","#92FE9D","#FF6B6B","#FFC300","#00C9FF","#F64F59","#FF6B9D"][n-1]}`,borderRadius:8,padding:"14px 16px",marginBottom:10}}>
              <div style={{color:["#00C9FF","#92FE9D","#FF6B6B","#FFC300","#00C9FF","#F64F59","#FF6B9D"][n-1],fontSize:11,fontWeight:700,letterSpacing:2,marginBottom:6}}>LAW {n} — {name}</div>
              <div style={{color:"#888",fontSize:12,lineHeight:1.7}}>{desc}</div>
            </div>
          ))}
        </div>
      )}

      {/* MEMORY TAB */}
      {tab==="memory"&&(
        <div style={{flex:1,overflowY:"auto",padding:16,maxWidth:720,margin:"0 auto",width:"100%",boxSizing:"border-box"}}>
          <div style={{fontSize:9,color:"#555",letterSpacing:3,marginBottom:14}}>LEARNED MEMORY — {learned} words</div>
          {Object.keys(learnedDict).length===0?(
            <div style={{background:"#10101A",borderRadius:8,padding:24,textAlign:"center",color:"#333",fontSize:12}}>
              Empty. Ask something not in the base dictionary and it saves here.
            </div>
          ):Object.entries(learnedDict).map(([word,data])=>(
            <div key={word} style={{background:"#10101A",borderLeft:"3px solid #F64F59",borderRadius:6,padding:"10px 14px",marginBottom:8}}>
              <div style={{color:"#F64F59",fontSize:11,fontWeight:700,marginBottom:3}}>{word}</div>
              <div style={{color:"#666",fontSize:11}}>{typeof data==="object"?data.definition:data}</div>
            </div>
          ))}
          <div style={{marginTop:16,background:"#10101A",border:"1px solid #92FE9D22",borderRadius:8,padding:14}}>
            <div style={{color:"#92FE9D",fontSize:9,letterSpacing:3,marginBottom:10}}>BASE DICTIONARY — {Object.keys(DICTIONARY).length} rich entries</div>
            <div style={{display:"flex",flexWrap:"wrap",gap:5}}>
              {Object.keys(DICTIONARY).map(w=>(
                <span key={w} style={{background:"#080810",borderRadius:4,padding:"2px 8px",fontSize:9,color:"#444"}}>{w}</span>
              ))}
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes fadeIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
        @keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
        input::placeholder{color:#2A2A3A}
        ::-webkit-scrollbar{width:3px}
        ::-webkit-scrollbar-track{background:#080810}
        ::-webkit-scrollbar-thumb{background:#1A1A2E;border-radius:2px}
      `}</style>
    </div>
  );
}
