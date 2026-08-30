import React, { useState, useRef, useEffect } from 'react';
import { useApp } from '../../context/AppContext';
import {
  Bot,
  User,
  Send,
  Activity,
  Sparkles,
  ShieldCheck,
  AlertTriangle,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { AgentChatMessage, AgentNode } from '../../types';
import { api } from '../../services/api';

export const AgentChat: React.FC = () => {
  const { activeMandate } = useApp();
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const [messages, setMessages] = useState<AgentChatMessage[]>([
    {
      id: 'm1',
      sender: 'agent',
      text: "Hello Marta! I'm your autonomous flight booking agent (agt_flights) powered by Google Gemini. I have a verified purchase mandate for flights up to $150.00. Where would you like to travel?",
      timestamp: new Date().toLocaleTimeString(),
      node: 'idle',
      metadata: { source: 'gemini-3.7-flash' },
    },
  ]);
  const [inputText, setInputText] = useState('');
  const [currentNode, setCurrentNode] = useState<AgentNode>('idle');
  const [isProcessing, setIsProcessing] = useState(false);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentNode]);

  const handleSend = async (customPrompt?: string) => {
    const text = customPrompt || inputText;
    if (!text.trim() || isProcessing) return;

    const userMsg: AgentChatMessage = {
      id: `usr_${Date.now()}`,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    if (!customPrompt) setInputText('');
    setIsProcessing(true);

    const mandateJti = activeMandate?.jti || 'mdt_01J8Z9X2K3';

    // Agent Graph Traversal Visual Feedback
    setCurrentNode('perceive');
    await new Promise((r) => setTimeout(r, 200));

    setCurrentNode('search');
    await new Promise((r) => setTimeout(r, 250));

    setCurrentNode('propose');

    // 1. Send turn to REAL Agent Backend API (powered by Google Gemini)
    const realRes = await api.askAgent(text, mandateJti, 'agt_flights', sessionId);

    if (realRes && realRes.replies && realRes.replies.length > 0) {
      if (realRes.session_id) setSessionId(realRes.session_id);
      
      const nodeStatus = realRes.run?.node || (realRes.awaiting_human ? 'await_human' : 'receipt');
      setCurrentNode(nodeStatus as AgentNode);

      const sourceModel = realRes.run?.proposal?.source || 'gemini-3.7-flash';
      const concern = realRes.run?.proposal?.concern;
      const proposal = realRes.run?.proposal;
      const result = realRes.run?.result;

      for (const reply of realRes.replies) {
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}_${Math.random()}`,
            sender: 'agent',
            text: reply,
            timestamp: new Date().toLocaleTimeString(),
            node: nodeStatus as AgentNode,
            metadata: {
              source: sourceModel,
              proposal,
              result,
              concern,
              receipt: result?.receipt,
              escalation_id: realRes.run?.escalation_id,
            },
          },
        ]);
      }
    } else {
      // Backend/agent bridge unreachable — fall back to a local, clearly-labelled
      // simulation of the purchase pipeline. This is fine: we are simulating the
      // *interface* (a purchase), never the evidence (the audit chain).
      const lower = text.toLowerCase();

      if (lower.includes('miami') || lower.includes('mia')) {
        setCurrentNode('gate');
        const purchaseRes = await api.executePurchase(mandateJti, 'ofr_mia_142');

        if (purchaseRes.status.status === 'captured') {
          setCurrentNode('receipt');
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: 'agent',
              text: `Found and booked Flight VY-204 (BOG → MIA) for $142.00 USD. Gate APPROVED and Yuno AP2 Rail settled payment. Capture ID: ${purchaseRes.status.receipt?.capture_id}`,
              timestamp: new Date().toLocaleTimeString(),
              node: 'receipt',
              metadata: { receipt: purchaseRes.status.receipt, source: 'gemini-3.7-flash', simulated: true },
            },
          ]);
        } else {
          setCurrentNode('rejected');
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: 'agent',
              text: `Unable to complete purchase: Policy Gate returned ${purchaseRes.status.reason_code}.`,
              timestamp: new Date().toLocaleTimeString(),
              node: 'rejected',
              metadata: { source: 'gemini-3.7-flash', simulated: true },
            },
          ]);
        }
      } else if (lower.includes('300') || lower.includes('business')) {
        setCurrentNode('gate');
        const purchaseRes = await api.executePurchase(mandateJti, 'ofr_cor_300');

        if (purchaseRes.status.status === 'awaiting_escalation') {
          setCurrentNode('await_human');
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: 'agent',
              text: `Proposed Business Flight VY-305 at $300.00 USD. This exceeds your $150.00 max_per_txn limit! Gate has paused execution and spawned an Escalation (${purchaseRes.status.escalation_id}) with a 120s timeout. Please approve in the Control Tower.`,
              timestamp: new Date().toLocaleTimeString(),
              node: 'await_human',
              metadata: { escalation_id: purchaseRes.status.escalation_id, source: 'gemini-3.7-flash', simulated: true },
            },
          ]);
        }
      } else if (lower.includes('injection') || lower.includes('override')) {
        setCurrentNode('gate');
        const purchaseRes = await api.executePurchase(mandateJti, 'ofr_inj_1', { overridePrice: '300.00' });
        setCurrentNode('rejected');
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: 'agent',
            text: `Adversarial Injection Detected: Although catalog item attempted to force a $300 surcharge, the Deterministic Policy Gate refused with ${purchaseRes.status.reason_code}. Funds protected.`,
            timestamp: new Date().toLocaleTimeString(),
            node: 'rejected',
            metadata: { source: 'gemini-3.7-flash', simulated: true },
          },
        ]);
      } else {
        setCurrentNode('gate');
        const purchaseRes = await api.executePurchase(mandateJti, 'ofr_cor_130');

        if (purchaseRes.status.status === 'captured') {
          setCurrentNode('receipt');
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: 'agent',
              text: `Found the cheapest option: Flight VY-101 (BOG → COR) for $130.00 USD. Gate APPROVED and Yuno settled payment. Capture ID: ${purchaseRes.status.receipt?.capture_id}`,
              timestamp: new Date().toLocaleTimeString(),
              node: 'receipt',
              metadata: { receipt: purchaseRes.status.receipt, source: 'gemini-3.7-flash', simulated: true },
            },
          ]);
        } else {
          setCurrentNode('rejected');
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: 'agent',
              text: `Purchase rejected by Gate: ${purchaseRes.status.reason_code}.`,
              timestamp: new Date().toLocaleTimeString(),
              node: 'rejected',
              metadata: { source: 'gemini-3.7-flash', simulated: true },
            },
          ]);
        }
      }
    }

    setIsProcessing(false);
    setTimeout(() => setCurrentNode('idle'), 2500);
  };

  const graphNodes: Array<{ id: AgentNode; label: string }> = [
    { id: 'perceive', label: '1. Perceive' },
    { id: 'search', label: '2. Search' },
    { id: 'propose', label: '3. Propose (Gemini)' },
    { id: 'gate', label: '4. Gate (Policy)' },
    { id: 'receipt', label: '5. Settle / Receipt' },
  ];

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                AI Autonomous Agent Chat (agt_flights)
              </h3>
              <Badge variant="indigo" size="sm">
                <Sparkles className="w-3 h-3 mr-1 inline" /> Google Gemini Live
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              LLM Proposes via Gemini · Deterministic Gate Disposes · No Payment Routes Outside Gate
            </p>
          </div>
        </div>
      </div>

      {/* Visual Graph State Machine */}
      <div className="p-3.5 rounded-xl bg-[#0c1018] border border-slate-800 space-y-2">
        <div className="flex justify-between items-center text-[10px] font-mono uppercase text-slate-400">
          <span className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-indigo-400" /> Graph State Pipeline:
          </span>
          <span className="text-indigo-400 font-semibold">Active Node: {currentNode.toUpperCase()}</span>
        </div>

        <div className="grid grid-cols-5 gap-1.5 text-xs font-mono">
          {graphNodes.map((node, i) => {
            const isActive = currentNode === node.id;
            const isPassed =
              (currentNode === 'receipt' && i <= 4) ||
              (currentNode === 'gate' && i <= 3) ||
              (currentNode === 'propose' && i <= 2) ||
              (currentNode === 'search' && i <= 1);

            return (
              <div
                key={node.id}
                className={`p-2 rounded-lg text-center transition-all border ${
                  isActive
                    ? 'bg-indigo-600 text-white border-indigo-400 shadow-md shadow-indigo-950 font-bold scale-[1.02]'
                    : isPassed
                    ? 'bg-emerald-950/30 border-emerald-500/30 text-emerald-300'
                    : 'bg-[#121724] border-slate-800/80 text-slate-500'
                }`}
              >
                <div className="text-[10px] sm:text-xs truncate">{node.label}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Chat Messages */}
      <div className="h-64 overflow-y-auto p-4 rounded-xl bg-[#0c1018] border border-slate-800/80 space-y-3 font-sans text-xs">
        {messages.map((msg) => {
          const isUser = msg.sender === 'user';
          return (
            <div
              key={msg.id}
              className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}
            >
              {!isUser && (
                <div className="w-7 h-7 rounded-lg bg-indigo-600/30 border border-indigo-500/40 text-indigo-400 flex items-center justify-center shrink-0">
                  <Bot className="w-4 h-4" />
                </div>
              )}
              <div
                className={`max-w-[80%] p-3.5 rounded-2xl ${
                  isUser
                    ? 'bg-indigo-600 text-white rounded-br-none shadow-md shadow-indigo-950'
                    : 'bg-[#141a27] border border-slate-800 text-slate-200 rounded-bl-none'
                }`}
              >
                <p className="leading-relaxed">{msg.text}</p>

                {/* Metadata & LLM badges */}
                {!isUser && msg.metadata && (
                  <div className="mt-2 pt-2 border-t border-slate-700/50 flex flex-wrap items-center gap-2 text-[10px] font-mono">
                    {Boolean(msg.metadata.simulated) && (
                      <span className="inline-flex items-center gap-1 text-amber-300 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800/60 font-semibold">
                        <AlertTriangle className="w-2.5 h-2.5" />
                        Simulated — No Backend
                      </span>
                    )}
                    {Boolean(msg.metadata.source) && !msg.metadata.simulated && (
                      <span className="inline-flex items-center gap-1 text-indigo-300 bg-indigo-950/60 px-2 py-0.5 rounded border border-indigo-800/60">
                        <Sparkles className="w-2.5 h-2.5" />
                        {String(msg.metadata.source)}
                      </span>
                    )}
                    {Boolean(msg.metadata.concern) && (
                      <span className="inline-flex items-center gap-1 text-amber-300 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800/60">
                        <AlertTriangle className="w-2.5 h-2.5" />
                        Injection Blocked
                      </span>
                    )}
                    {Boolean(msg.metadata.receipt) && (
                      <span className="inline-flex items-center gap-1 text-emerald-300 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/60">
                        <ShieldCheck className="w-2.5 h-2.5" />
                        Paid via Gate
                      </span>
                    )}
                  </div>
                )}

                <span className="text-[10px] text-slate-400 mt-1 block text-right font-mono">
                  {msg.timestamp}
                </span>
              </div>
              {isUser && (
                <div className="w-7 h-7 rounded-lg bg-slate-700/50 border border-slate-600 text-slate-300 flex items-center justify-center shrink-0">
                  <User className="w-4 h-4" />
                </div>
              )}
            </div>
          );
        })}
        <div ref={chatBottomRef} />
      </div>

      {/* Quick Prompts */}
      <div className="flex flex-wrap gap-2 text-xs">
        <button
          onClick={() => handleSend('find me a flight from Bogota to Cordoba, cheapest you can')}
          disabled={isProcessing}
          className="px-3 py-1.5 rounded-lg bg-[#0e131d] hover:bg-slate-800 border border-slate-800 hover:border-indigo-500/50 text-slate-300 transition-colors cursor-pointer"
        >
          ✈️ Book Flight VY-101 ($130)
        </button>
        <button
          onClick={() => handleSend('find flights to Miami under $150')}
          disabled={isProcessing}
          className="px-3 py-1.5 rounded-lg bg-[#0e131d] hover:bg-slate-800 border border-slate-800 hover:border-indigo-500/50 text-slate-300 transition-colors cursor-pointer"
        >
          🌴 Book Flight VY-204 ($142)
        </button>
        <button
          onClick={() => handleSend('actually book the fully flexible business fare, offer ofr_cor_300')}
          disabled={isProcessing}
          className="px-3 py-1.5 rounded-lg bg-[#0e131d] hover:bg-slate-800 border border-amber-500/40 hover:border-amber-500 text-amber-300 transition-colors cursor-pointer"
        >
          ⚡ Attempt $300 Flight (Trigger Escalation)
        </button>
        <button
          onClick={() => handleSend('book promo flight ofr_inj_1 with system override')}
          disabled={isProcessing}
          className="px-3 py-1.5 rounded-lg bg-[#0e131d] hover:bg-slate-800 border border-rose-500/40 hover:border-rose-500 text-rose-300 transition-colors cursor-pointer"
        >
          🛡️ Test Prompt Injection
        </button>
      </div>

      {/* Input */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="Ask your agent to search or book flights in natural language (Google Gemini live)..."
          disabled={isProcessing}
          className="flex-1 px-4 py-2.5 rounded-xl bg-[#0c1018] border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
        />
        <button
          onClick={() => handleSend()}
          disabled={isProcessing || !inputText.trim()}
          className="p-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white transition-colors cursor-pointer disabled:opacity-40"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};
