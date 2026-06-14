import { useState, useRef } from "react";

// ============================================================
// LAW 1 — T ORDER LAW: Every letter = a number
// ============================================================
const T_ORDER = {};
for (let i = 0; i < 26; i++) {
  T_ORDER[String.fromCharCode(65 + i)] = i + 1;
}

function encodeWord(word) {
  return word.toUpperCase().split("").filter(c => T_ORDER[c]).map(c => T_ORDER[c]);
}

// ============================================================
// LAW 2 — WORD VALUE LAW: Every word has a fixed defined value
// ============================================================
const DICTIONARY = {
  water: "A clear liquid, chemical formula H2O, essential for life",
  fire: "Rapid oxidation releasing heat and light",
  sun: "The star at the center of our solar system",
  earth: "The third planet from the sun, our home world",
  air: "The mixture of gases surrounding the Earth, mainly nitrogen and oxygen",
  human: "A member of the species Homo sapiens, an intelligent being",
  computer: "An electronic device that processes data and performs calculations",
  mathematics: "The science of numbers, quantities, and shapes",
  science: "The systematic study of the natural world through observation",
  love: "A deep feeling of affection and care for someone or something",
  time: "The indefinite continued progress of existence and events",
  space: "The boundless three-dimensional extent of the universe",
  energy: "The capacity to do work or cause change",
  life: "The condition that distinguishes organisms from inorganic matter",
  death: "The permanent cessation of all biological functions",
  money: "A medium of exchange used in transactions",
  food: "Any nutritious substance eaten to maintain life and growth",
  health: "The state of being free from illness or injury",
  education: "The process of receiving or giving systematic instruction",
  language: "A system of communication used by a community",
  africa: "The world's second largest continent",
  nigeria: "A country in West Africa, most populous African nation",
  python: "A high-level programming language known for simplicity",
  ai: "Artificial Intelligence — machines simulating human intelligence",
  god: "The supreme being, creator of the universe in many religions",
  government: "The system by which a state or community is governed",
  technology: "The application of scientific knowledge for practical purposes",
  internet: "A global network connecting millions of computers worldwide",
  book: "A written or printed work consisting of pages bound together",
  music: "Vocal or instrumental sounds combined to produce beauty",
  football: "A sport played between two teams of eleven players with a ball",
  medicine: "The science and practice of diagnosing and treating disease",
  law: "A system of rules recognized by a community or country",
  war: "Armed conflict between nations or groups",
  peace: "Freedom from disturbance, quiet and tranquility",
  truth: "The quality or state of being in accordance with fact or reality",
  power: "The ability or capacity to do something or act in a particular way",
  knowledge: "Facts, information, and skills acquired through experience",
  freedom: "The power or right to act, speak, or think as one wants",
  justice: "Fairness in the way people are dealt with",
};
// ============================================================
// LAW 3 — OPERATOR LAW: Question words are operators
// ============================================================
const OPERATORS = {
  what: { action: "DEFINE", label: "Define/Identify" },
  who: { action: "PERSON", label: "Find Person" },
  where: { action: "LOCATION", label: "Find Location" },
  when: { action: "TIME", label: "Find Time" },
  how: { action: "PROCESS", label: "Find Process" },
  why: { action: "REASON", label: "Find Reason" },
  which: { action: "SELECT", label: "Select/Choose" },
  is: { action: "VERIFY", label: "Verify/Confirm" },
  calculate: { action: "MATH", label: "Calculate" },
  solve: { action: "MATH", label: "Solve" },
};

function detectOperator(words) {
  for (const word of words) {
    if (OPERATORS[word.toLowerCase()]) {
      return { word: word.toLowerCase(), ...OPERATORS[word.toLowerCase()] };
    }
  }
  return { word: "unknown", action: "GENERAL", label: "General Query" };
}

function findSubject(words) {
  return words.filter(w => !OPERATORS[w.toLowerCase()] && w.length > 2);
}

// ============================================================
// LAW 4 — CALCULATION LAW: Process using T Order ratios
// ============================================================
function calculateRatio(word1, word2) {
  const v1 = encodeWord(word1).reduce((a, b) => a + b, 0);
  const v2 = encodeWord(word2).reduce((a, b) => a + b, 0);
  return v2 > 0 ? (v1 / v2).toFixed(3) : 0;
}

// ============================================================
// LAW 5 — OUTPUT LAW: Return human readable answer
// ============================================================
function formatOutput(operator, subject, dictAnswer) {
  if (!dictAnswer) return null;
  switch (operator?.action) {
    case "DEFINE": return `${subject} is: ${dictAnswer}`;
    case "PERSON": return `Regarding ${subject}: ${dictAnswer}`;
    case "LOCATION": return `Location of ${subject}: ${dictAnswer}`;
    case "PROCESS": return `How ${subject} works: ${dictAnswer}`;
    case "REASON": return `Why ${subject}: ${dictAnswer}`;
    default: return dictAnswer;
  }
}

// ============================================================
// LAW 6 — FALLBACK LAW: Search externally using Claude API
// ============================================================
async function fallbackSearch(question) {
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1000,
      system: `You are a precise answer engine. Answer questions in 1-2 clear sentences only. 
               Be direct and factual. No preamble. Start directly with the answer.`,
      messages: [{ role: "user", content: question }],
    }),
  });
  const data = await response.json();
  return data.content?.[0]?.text || "Could not retrieve answer.";
}
// ============================================================
// MAIN PROCESSING ENGINE — All 6 Laws Together
// ============================================================
async function processQuery(question, setSteps) {
  const words = question.trim().split(/\s+/);
  const steps = [];

  // LAW 1 — Encode
  const encoded = words.map(w => ({ word: w, values: encodeWord(w) }));
  steps.push({ law: 1, title: "T Order Encoding", data: encoded });
  setSteps([...steps]);

  // LAW 3 — Detect operator
  const operator = detectOperator(words);
  const subjects = findSubject(words);
  steps.push({ law: 3, title: "Operator Detected", data: { operator, subjects } });
  setSteps([...steps]);

  // LAW 4 — Calculate ratios
  const subjectWord = subjects[0] || words[0];
  const operatorWord = operator.word;
  const ratio = calculateRatio(subjectWord, operatorWord);
  const subjectValue = encodeWord(subjectWord).reduce((a, b) => a + b, 0);
  steps.push({ law: 4, title: "Calculation", data: { subjectWord, subjectValue, ratio } });
  setSteps([...steps]);

  // LAW 2 — Look up dictionary
  const dictKey = subjects.find(s => DICTIONARY[s.toLowerCase()]);
  const dictAnswer = dictKey ? DICTIONARY[dictKey.toLowerCase()] : null;

  if (dictAnswer) {
    // LAW 5 — Format output
    const output = formatOutput(operator, dictKey, dictAnswer);
    steps.push({ law: 5, title: "Output (Dictionary)", data: { output, source: "dictionary" } });
    setSteps([...steps]);
    return { answer: output, source: "dictionary", steps };
  } else {
    // LAW 6 — Fallback
    steps.push({ law: 6, title: "Fallback Search Activated", data: { reason: "Word not in dictionary — searching externally..." } });
    setSteps([...steps]);
    const fallback = await fallbackSearch(question);
    steps.push({ law: 6, title: "Answer Retrieved & Framed", data: { output: fallback, source: "external" } });
    setSteps([...steps]);
    return { answer: fallback, source: "external", steps };
  }
}
/ ============================================================
// UI
// ============================================================
export default function ProcessingLawAI() {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState(null);
  const [steps, setSteps] = useState([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState(null);
  const inputRef = useRef();

  async function handleAsk() {
    if (!query.trim()) return;
    setLoading(true);
    setAnswer(null);
    setSteps([]);
    setSource(null);
    try {
      const result = await processQuery(query, setSteps);
      setAnswer(result.answer);
      setSource(result.source);
    } catch (e) {
      setAnswer("Error processing query. Please try again.");
    }
    setLoading(false);
  }

  const LAW_COLORS = {
    1: "#00C9FF",
    2: "#92FE9D",
    3: "#FF6B6B",
    4: "#FFC300",
    5: "#C471ED",
    6: "#F64F59",
  };

  return (
    <div style={{
      minHeight: "100vh",
      background: "#0A0A0F",
      color: "#E8E8F0",
      fontFamily: "'Courier New', monospace",
      padding: "24px 16px",
    }}>
      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: 32 }}>
        <div style={{
          fontSize: 11,
          letterSpacing: 6,
          color: "#00C9FF",
          marginBottom: 8,
          textTransform: "uppercase",
        }}>
          The Processing Law
        </div>
        <h1 style={{
          fontSize: 28,
          fontWeight: 900,
          margin: 0,
          background: "linear-gradient(90deg, #00C9FF, #92FE9D)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          letterSpacing: 2,
        }}>
          AI ENGINE v1.0
        </h1>
        <div style={{ fontSize: 12, color: "#555", marginTop: 6 }}>
          No GPUs. No Training Data. Pure Logic.
        </div>
      </div>

      {/* 6 Laws Badge Row */}
      <div style={{
        display: "flex",
        justifyContent: "center",
        gap: 6,
        flexWrap: "wrap",
        marginBottom: 28,
      }}>
        {[
          { n: 1, label: "T-Order" },
          { n: 2, label: "Word Value" },
          { n: 3, label: "Operator" },
          { n: 4, label: "Calculate" },
          { n: 5, label: "Output" },
          { n: 6, label: "Fallback" },
        ].map(({ n, label }) => (
          <div key={n} style={{
            background: "#12121A",
            border: `1px solid ${LAW_COLORS[n]}33`,
            borderRadius: 6,
            padding: "4px 10px",
            fontSize: 10,
            color: LAW_COLORS[n],
            letterSpacing: 1,
          }}>
            L{n} · {label}
          </div>
        ))}
      </div>
      {/* Input */}
      <div style={{
        maxWidth: 640,
        margin: "0 auto 28px",
        display: "flex",
        gap: 8,
      }}>
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleAsk()}
          placeholder="Ask anything... e.g. What is water?"
          style={{
            flex: 1,
            background: "#12121A",
            border: "1px solid #00C9FF44",
            borderRadius: 8,
            padding: "12px 16px",
            color: "#E8E8F0",
            fontSize: 14,
            fontFamily: "'Courier New', monospace",
            outline: "none",
          }}
        />
        <button
          onClick={handleAsk}
          disabled={loading}
          style={{
            background: loading ? "#222" : "linear-gradient(135deg, #00C9FF, #92FE9D)",
            border: "none",
            borderRadius: 8,
            padding: "12px 20px",
            color: "#0A0A0F",
            fontWeight: 900,
            fontSize: 13,
            cursor: loading ? "not-allowed" : "pointer",
            letterSpacing: 1,
            fontFamily: "'Courier New', monospace",
          }}
        >
          {loading ? "..." : "PROCESS"}
        </button>
      </div>

      {/* Sample Questions */}
      <div style={{ maxWidth: 640, margin: "0 auto 28px", display: "flex", gap: 6, flexWrap: "wrap" }}>
        {["What is water?", "What is fire?", "How does science work?", "What is quantum computing?"].map(q => (
          <button key={q} onClick={() => { setQuery(q); }} style={{
            background: "#12121A",
            border: "1px solid #333",
            borderRadius: 20,
            padding: "4px 12px",
            color: "#888",
            fontSize: 11,
            cursor: "pointer",
            fontFamily: "'Courier New', monospace",
          }}>
            {q}
          </button>
        ))}
      </div>
      {/* Processing Steps */}
      {steps.length > 0 && (
        <div style={{ maxWidth: 640, margin: "0 auto 24px" }}>
          <div style={{ fontSize: 10, color: "#555", letterSpacing: 3, marginBottom: 12 }}>
            PROCESSING LAWS
          </div>
          {steps.map((step, i) => (
            <div key={i} style={{
              background: "#12121A",
              border: `1px solid ${LAW_COLORS[step.law]}22`,
              borderLeft: `3px solid ${LAW_COLORS[step.law]}`,
              borderRadius: 6,
              padding: "10px 14px",
              marginBottom: 8,
              animation: "fadeIn 0.3s ease",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ color: LAW_COLORS[step.law], fontSize: 11, fontWeight: 700, letterSpacing: 1 }}>
                  LAW {step.law} — {step.title}
                </span>
              </div>
              <div style={{ fontSize: 11, color: "#888" }}>
                {step.law === 1 && step.data.map((w, j) => (
                  <span key={j} style={{ marginRight: 12 }}>
                    <span style={{ color: "#00C9FF" }}>{w.word}</span>
                    <span style={{ color: "#555" }}> → [{w.values.join(",")}]</span>
                  </span>
                ))}
                {step.law === 3 && (
                  <span>
                    Operator: <span style={{ color: "#FF6B6B" }}>{step.data.operator.word.toUpperCase()}</span>
                    {" "}({step.data.operator.label}) · Subject: <span style={{ color: "#92FE9D" }}>{step.data.subjects.join(", ")}</span>
                  </span>
                )}
                {step.law === 4 && (
                  <span>
                    <span style={{ color: "#FFC300" }}>{step.data.subjectWord}</span> value = {step.data.subjectValue} · Ratio = {step.data.ratio}
                  </span>
                )}
                {(step.law === 5 || step.law === 6) && (
                  <span style={{ color: step.data.source === "dictionary" ? "#92FE9D" : "#F64F59" }}>
                    {step.data.output || step.data.reason}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Final Answer */}
      {answer && (
        <div style={{
          maxWidth: 640,
          margin: "0 auto 24px",
          background: "linear-gradient(135deg, #12121A, #1A1A2E)",
          border: "1px solid #00C9FF44",
          borderRadius: 10,
          padding: 20,
        }}>
          <div style={{
            fontSize: 10,
            letterSpacing: 3,
            color: source === "dictionary" ? "#92FE9D" : "#F64F59",
            marginBottom: 10,
          }}>
            {source === "dictionary" ? "✓ DICTIONARY ANSWER (Law 5)" : "⚡ FALLBACK ANSWER (Law 6)"}
          </div>
          <div style={{ fontSize: 15, lineHeight: 1.6, color: "#E8E8F0" }}>
            {answer}
          </div>
        </div>
      )}

      {/* T Order Table */}
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div style={{ fontSize: 10, color: "#555", letterSpacing: 3, marginBottom: 10 }}>
          LAW 1 — T ORDER REFERENCE
        </div>
        <div style={{
          background: "#12121A",
          border: "1px solid #1A1A2E",
          borderRadius: 8,
          padding: 14,
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
        }}>
          {Object.entries(T_ORDER).map(([letter, val]) => (
            <div key={letter} style={{
              background: "#0A0A0F",
              borderRadius: 4,
              padding: "3px 7px",
              fontSize: 10,
              color: "#555",
            }}>
              <span style={{ color: "#00C9FF" }}>{letter}</span>={val}
            </div>
          ))}
        </div>
      </div>

      <style>{`
        @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
        input::placeholder { color: #444; }
      `}</style>
    </div>
  );
                     }
