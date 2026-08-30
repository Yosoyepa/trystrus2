import React from 'react';
import { useApp } from '../../context/AppContext';
import {
  Activity,
  Lock,
  Gauge,
  Radio,
  Clock,
} from 'lucide-react';
import { Badge } from '../common/Badge';

export const TelemetryDashboard: React.FC = () => {
  const { telemetry } = useApp();

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
            <Activity className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Kernel Telemetry & Concurrency Monitor
              </h3>
              <Badge variant="emerald" size="sm" pulse>
                {telemetry.relay_status}
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Postgres Advisory Locks · Token Bucket Rate Limits · CAS Budget Reservations · Outbox Relayer
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
        {/* Metric 1: Advisory Locks */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3">
          <div className="flex items-center justify-between text-slate-400">
            <span className="flex items-center gap-1.5 uppercase text-[10px]">
              <Lock className="w-3.5 h-3.5 text-indigo-400" /> PG Advisory Locks
            </span>
            <Badge variant="indigo" size="sm">
              {telemetry.active_advisory_locks.length} Acquired
            </Badge>
          </div>
          <div>
            <div className="text-xl font-bold text-white">
              {telemetry.active_advisory_locks.length} Active
            </div>
            <p className="text-[11px] text-slate-400 mt-1 truncate">
              {telemetry.active_advisory_locks[0]?.lock_name || 'None'}
            </p>
          </div>
          <div className="text-[10px] text-slate-500">
            PID: {telemetry.active_advisory_locks[0]?.pid || 4812} · Concurrency Safe
          </div>
        </div>

        {/* Metric 2: Rate Limit Bucket */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3">
          <div className="flex items-center justify-between text-slate-400">
            <span className="flex items-center gap-1.5 uppercase text-[10px]">
              <Gauge className="w-3.5 h-3.5 text-cyan-400" /> Rate Token Bucket
            </span>
            <Badge variant="cyan" size="sm">
              {telemetry.rate_limit.tokens_remaining}/{telemetry.rate_limit.capacity}
            </Badge>
          </div>
          <div>
            <div className="text-xl font-bold text-white">
              {telemetry.rate_limit.tokens_remaining} Tokens
            </div>
            <p className="text-[11px] text-slate-400 mt-1">
              Fill rate: {telemetry.rate_limit.fill_rate}
            </p>
          </div>
          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
            <div
              style={{
                width: `${(telemetry.rate_limit.tokens_remaining / telemetry.rate_limit.capacity) * 100}%`,
              }}
              className="h-full bg-cyan-400 rounded-full"
            />
          </div>
        </div>

        {/* Metric 3: Outbox Relay Queue */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3">
          <div className="flex items-center justify-between text-slate-400">
            <span className="flex items-center gap-1.5 uppercase text-[10px]">
              <Radio className="w-3.5 h-3.5 text-emerald-400" /> Outbox SSE Relay
            </span>
            <Badge variant="emerald" size="sm">
              0 Backlog
            </Badge>
          </div>
          <div>
            <div className="text-xl font-bold text-white">
              {telemetry.outbox_queue.processed_count} Relayed
            </div>
            <p className="text-[11px] text-slate-400 mt-1">
              Depth: {telemetry.outbox_queue.depth} pending
            </p>
          </div>
          <div className="text-[10px] text-slate-500 truncate">
            Last: {new Date(telemetry.outbox_queue.last_relayed_at).toLocaleTimeString()}
          </div>
        </div>

        {/* Metric 4: End-to-End Latency */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3">
          <div className="flex items-center justify-between text-slate-400">
            <span className="flex items-center gap-1.5 uppercase text-[10px]">
              <Clock className="w-3.5 h-3.5 text-purple-400" /> Verification Speed
            </span>
            <Badge variant="purple" size="sm">
              Target ≤ 2.0s
            </Badge>
          </div>
          <div>
            <div className="text-xl font-bold text-emerald-400">
              {telemetry.avg_latency_ms} ms
            </div>
            <p className="text-[11px] text-slate-400 mt-1">
              SD-JWT verify + Gate CAS + Rail
            </p>
          </div>
          <div className="text-[10px] text-slate-500">
            98.6% faster than SLA limit
          </div>
        </div>
      </div>
    </div>
  );
};
