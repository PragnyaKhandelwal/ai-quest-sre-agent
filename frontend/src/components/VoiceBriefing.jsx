/**
 * Voice Briefing Agent -- Stretch Goal
 * Reads out the current incident summary using the Web Speech API (no
 * external deps). Simulates a real-time incident war-room audio briefing.
 */
import { useState } from "react";

function generateBriefing(incident) {
  if (!incident) return "No active incident selected.";

  const sev = incident.triage?.severity || "unknown severity";
  const service = (incident.triage?.affected_services || []).join(", ") || "an unknown service";
  const cause = incident.diagnosis?.cause || "still under investigation";
  const status = incident.status || "unknown";
  const pendingCount = (incident.pending_hitl || []).length;
  const hitl =
    pendingCount > 0
      ? `Human approval is currently required for ${pendingCount} destructive action${pendingCount > 1 ? "s" : ""}.`
      : "No human approval is currently pending.";

  return (
    `Incident briefing. Severity ${sev} incident detected on ${service}. ` +
    `Current status: ${status}. Root cause: ${cause}. ${hitl} ` +
    `This is an automated SRE agent briefing powered by Lyzr Automata.`
  );
}

export default function VoiceBriefing({ incident }) {
  const [speaking, setSpeaking] = useState(false);

  const speak = () => {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(generateBriefing(incident));
    utterance.rate = 0.95;
    utterance.pitch = 1.0;
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
  };

  const stop = () => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  };

  if (typeof window !== "undefined" && !window.speechSynthesis) return null;

  return (
    <button
      onClick={speaking ? stop : speak}
      disabled={!incident}
      className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-mono-log border transition disabled:opacity-40 disabled:cursor-not-allowed
        ${
          speaking
            ? "bg-danger/20 text-danger border-danger/50 animate-pulse"
            : "bg-black/30 text-cyan border-cyan/40 hover:border-cyan"
        }`}
      title="Voice Briefing Agent (Stretch Goal)"
    >
      {speaking ? "🔴 Stop Briefing" : "🎙️ Voice Brief"}
    </button>
  );
}
