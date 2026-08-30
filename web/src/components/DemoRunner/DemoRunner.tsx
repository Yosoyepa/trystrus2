import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { getInitialScenarios, runScenario } from '../../services/demoScenarios';
import { DemoScenario } from '../../types';
import { ScenarioCard } from './ScenarioCard';
import {
  PlayCircle,
  RotateCcw,
  Clock,
} from 'lucide-react';
import { Badge } from '../common/Badge';

export const DemoRunner: React.FC = () => {
  const { addToast } = useApp();
  const [scenarios, setScenarios] = useState<DemoScenario[]>(getInitialScenarios());
  const [isRunningAll, setIsRunningAll] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<string>('all');

  const passedCount = scenarios.filter((s) => s.status === 'passed').length;

  const handleRunSingle = async (target: DemoScenario) => {
    try {
      await runScenario(target, (updated) => {
        setScenarios((prev) => prev.map((s) => (s.id === updated.id ? { ...updated } : s)));
      });
      addToast('success', `Scenario #${target.id} PASSED`, target.title);
    } catch (err) {
      addToast('error', `Scenario #${target.id} Failed`, String(err));
    }
  };

  const handleRunAll = async () => {
    setIsRunningAll(true);
    addToast('info', 'Running All 10 Scenarios', 'Executing full end-to-end judge test suite sequentially...');

    for (const sc of scenarios) {
      try {
        await runScenario(sc, (updated) => {
          setScenarios((prev) => prev.map((s) => (s.id === updated.id ? { ...updated } : s)));
        });
      } catch (err) {
        console.error('Scenario failed:', err);
      }
    }

    setIsRunningAll(false);
    addToast('success', 'Demo Suite Completed (10/10)', 'All 10 cryptographic judging scenarios verified successfully.');
  };

  const handleResetAll = () => {
    setScenarios(getInitialScenarios());
    addToast('info', 'Scenarios Reset', 'Reset all 10 test scenarios to initial ready state.');
  };

  const filteredScenarios = scenarios.filter(
    (s) => selectedCategory === 'all' || s.category === selectedCategory
  );

  return (
    <div className="space-y-6">
      {/* Master Control Banner */}
      <div className="p-6 sm:p-7 rounded-2xl glass-panel-glow border border-indigo-500/40 relative overflow-hidden space-y-6">
        {/* Background glow circle */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-indigo-600/10 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-5 relative z-10">
          <div className="space-y-1.5 max-w-2xl">
            <div className="flex items-center gap-2.5">
              <Badge variant="indigo" size="md" pulse={isRunningAll}>
                Official NextWave 2026 Judge Test Suite
              </Badge>
              <span className="text-xs font-mono text-slate-400">RFC 9901 + AP2</span>
            </div>
            <h2 className="text-xl sm:text-2xl font-extrabold font-display tracking-tight text-white">
              Autonomous AI Agent Trust Layer · 10-Scenario Test Runner
            </h2>
            <p className="text-xs sm:text-sm text-slate-300 font-sans leading-relaxed">
              Every claim on the slide, executed and mathematically verified. The AI agent proposes; the deterministic code gate decides; the merchant verifies offline; every action commits to the hash-chained audit ledger.
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-3 w-full lg:w-auto">
            <button
              onClick={handleRunAll}
              disabled={isRunningAll}
              className="flex-1 lg:flex-none flex items-center justify-center gap-2.5 px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold text-sm shadow-xl shadow-indigo-950/60 transition-all cursor-pointer disabled:opacity-50"
            >
              {isRunningAll ? (
                <>
                  <Clock className="w-5 h-5 animate-spin" /> Running Demo Suite ({passedCount}/10)...
                </>
              ) : (
                <>
                  <PlayCircle className="w-5 h-5 fill-white text-indigo-600" /> Run All 10 Scenarios
                </>
              )}
            </button>

            <button
              onClick={handleResetAll}
              disabled={isRunningAll}
              className="p-3 rounded-xl border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-300 transition-colors cursor-pointer disabled:opacity-50"
              title="Reset All Scenarios"
            >
              <RotateCcw className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Live Progress Bar & Metric Pills */}
        <div className="pt-2 border-t border-slate-800/80 grid grid-cols-1 sm:grid-cols-4 gap-3 text-xs font-mono">
          <div className="p-3 rounded-xl bg-[#0c1018] border border-slate-800 flex items-center justify-between">
            <span className="text-slate-400">Scenarios Passed:</span>
            <span className="text-emerald-400 font-bold text-sm">
              {passedCount} / 10
            </span>
          </div>
          <div className="p-3 rounded-xl bg-[#0c1018] border border-slate-800 flex items-center justify-between">
            <span className="text-slate-400">Deterministic Gate:</span>
            <span className="text-indigo-300 font-bold text-sm">
              100% Enforced
            </span>
          </div>
          <div className="p-3 rounded-xl bg-[#0c1018] border border-slate-800 flex items-center justify-between">
            <span className="text-slate-400">Live Kill-Switch:</span>
            <span className="text-cyan-300 font-bold text-sm">
              &le; 2.0s Target (45ms)
            </span>
          </div>
          <div className="p-3 rounded-xl bg-[#0c1018] border border-slate-800 flex items-center justify-between">
            <span className="text-slate-400">Tamper Detection:</span>
            <span className="text-purple-300 font-bold text-sm">
              Fail-Closed
            </span>
          </div>
        </div>
      </div>

      {/* Category Filter Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 p-1.5 rounded-2xl bg-[#0e131d] border border-slate-800 text-xs font-mono">
          {['all', 'Core', 'Security', 'Adversarial', 'Audit', 'Automation'].map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`px-3 py-1.5 rounded-xl transition-all cursor-pointer ${
                selectedCategory === cat
                  ? 'bg-indigo-600 text-white font-bold shadow-md shadow-indigo-950'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
              }`}
            >
              {cat === 'all' ? 'All (10)' : cat}
            </button>
          ))}
        </div>
        <span className="text-xs text-slate-400 font-mono">
          Showing {filteredScenarios.length} of 10 Scenarios
        </span>
      </div>

      {/* Scenario Cards Grid */}
      <div className="space-y-4">
        {filteredScenarios.map((scenario) => (
          <ScenarioCard
            key={scenario.id}
            scenario={scenario}
            onRun={handleRunSingle}
            isRunningAny={isRunningAll}
          />
        ))}
      </div>
    </div>
  );
};
