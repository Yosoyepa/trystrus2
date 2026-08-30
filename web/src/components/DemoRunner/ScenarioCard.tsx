import React, { useState } from 'react';
import { DemoScenario } from '../../types';
import {
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { CodeBlock } from '../common/CodeBlock';

interface ScenarioCardProps {
  scenario: DemoScenario;
  onRun: (scenario: DemoScenario) => void;
  isRunningAny: boolean;
}

export const ScenarioCard: React.FC<ScenarioCardProps> = ({
  scenario,
  onRun,
  isRunningAny,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const getStatusBadge = (status: DemoScenario['status']) => {
    switch (status) {
      case 'passed':
        return (
          <Badge variant="emerald" size="sm">
            <CheckCircle2 className="w-3 h-3" /> PASS
          </Badge>
        );
      case 'failed':
        return (
          <Badge variant="rose" size="sm">
            <XCircle className="w-3 h-3" /> FAIL
          </Badge>
        );
      case 'running':
        return (
          <Badge variant="indigo" size="sm" pulse>
            <Clock className="w-3 h-3 animate-spin" /> RUNNING
          </Badge>
        );
      default:
        return (
          <Badge variant="slate" size="sm">
            READY
          </Badge>
        );
    }
  };

  const getCategoryBadge = (cat: DemoScenario['category']) => {
    switch (cat) {
      case 'Security':
      case 'Adversarial':
        return <Badge variant="rose" size="sm">{cat}</Badge>;
      case 'Audit':
        return <Badge variant="purple" size="sm">{cat}</Badge>;
      case 'Automation':
        return <Badge variant="cyan" size="sm">{cat}</Badge>;
      default:
        return <Badge variant="indigo" size="sm">{cat}</Badge>;
    }
  };

  return (
    <div
      className={`rounded-2xl border transition-all ${
        scenario.status === 'passed'
          ? 'bg-[#0e161c] border-emerald-500/40 shadow-lg shadow-emerald-950/20'
          : scenario.status === 'failed'
          ? 'bg-[#1a0e14] border-rose-500/40 shadow-lg shadow-rose-950/20'
          : scenario.status === 'running'
          ? 'bg-[#121626] border-indigo-500/60 shadow-xl shadow-indigo-950/40'
          : 'glass-panel border-slate-800/80 hover:border-slate-700'
      } p-5 space-y-4`}
    >
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-start sm:items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-indigo-600/20 border border-indigo-500/40 text-indigo-400 flex items-center justify-center font-bold text-sm shrink-0 font-mono">
            {scenario.id}
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm sm:text-base font-bold font-display text-white">
                {scenario.title}
              </h3>
              {getCategoryBadge(scenario.category)}
              {getStatusBadge(scenario.status)}
            </div>
            <p className="text-xs text-indigo-300/90 font-mono mt-0.5">
              {scenario.tagline}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
          <button
            onClick={() => onRun(scenario)}
            disabled={isRunningAny}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md shadow-indigo-950/40 transition-all cursor-pointer disabled:opacity-40"
          >
            <Play className="w-3.5 h-3.5 fill-white" />
            <span>Run Test</span>
          </button>

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1.5 rounded-xl border border-slate-800 bg-[#0c1018] hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
            title="Toggle Step Details"
          >
            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-400 font-sans leading-relaxed">
        {scenario.description}
      </p>

      {/* Step by Step Execution Flow */}
      <div className="space-y-2 pt-1">
        {scenario.steps.map((step, idx) => {
          return (
            <div
              key={step.id}
              className={`p-2.5 rounded-xl border transition-all text-xs font-mono flex items-start gap-2.5 ${
                step.status === 'passed'
                  ? 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'
                  : step.status === 'failed'
                  ? 'bg-rose-950/20 border-rose-500/30 text-rose-300'
                  : step.status === 'running'
                  ? 'bg-indigo-950/30 border-indigo-500/50 text-indigo-200'
                  : 'bg-[#0c1018]/60 border-slate-800/60 text-slate-400'
              }`}
            >
              <div className="mt-0.5">
                {step.status === 'passed' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                ) : step.status === 'failed' ? (
                  <XCircle className="w-4 h-4 text-rose-400" />
                ) : step.status === 'running' ? (
                  <Clock className="w-4 h-4 text-indigo-400 animate-spin" />
                ) : (
                  <span className="w-4 h-4 rounded-full border border-slate-700 flex items-center justify-center text-[9px] text-slate-500">
                    {idx + 1}
                  </span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">{step.title}</span>
                  <span className="text-[10px] uppercase">{step.status}</span>
                </div>
                <p className="text-[11px] text-slate-400 font-sans mt-0.5">{step.description}</p>
                {step.details && (
                  <p className="text-[10px] text-indigo-300 mt-1 font-mono bg-black/40 p-1.5 rounded border border-indigo-500/20">
                    {step.details}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Expanded Evidence Envelope Preview */}
      {isExpanded && scenario.evidence && (
        <div className="pt-2 border-t border-slate-800">
          <CodeBlock
            code={scenario.evidence}
            title={`Scenario #${scenario.id} Cryptographic Evidence Pack`}
            maxHeight="max-h-48"
          />
        </div>
      )}
    </div>
  );
};
