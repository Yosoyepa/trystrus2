import React from 'react';
import { useApp } from './context/AppContext';
import { Header } from './components/Header';
import { ToastContainer } from './components/ToastContainer';
import { AuditorTower } from './components/AuditorTower/AuditorTower';
import { BuyerConsole } from './components/BuyerConsole/BuyerConsole';
import { MerchantConsole } from './components/MerchantConsole/MerchantConsole';
import { DemoRunner } from './components/DemoRunner/DemoRunner';
import { Shield, Lock, Terminal, Cpu, GitBranch } from 'lucide-react';

export const AppContent: React.FC = () => {
  const { activeTab } = useApp();

  return (
    <div className="min-h-screen flex flex-col bg-[#080b11] text-slate-100 font-sans selection:bg-indigo-500 selection:text-white">
      {/* Top Header */}
      <Header />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8">
        {activeTab === 'demo' && <DemoRunner />}
        {activeTab === 'auditor' && <AuditorTower />}
        {activeTab === 'buyer' && <BuyerConsole />}
        {activeTab === 'merchant' && <MerchantConsole />}
      </main>

      {/* Modern High-Polish Footer */}
      <footer className="mt-auto border-t border-slate-800/80 bg-[#0a0d14] py-6 text-xs text-slate-400 font-mono">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-1.5 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
              <Shield className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-slate-200">Aval (TryTrust)</span>
                <span className="text-slate-500">·</span>
                <span>NextWave Hackathon 2026</span>
              </div>
              <p className="text-[11px] text-slate-500 font-sans">
                The buyer who isn't human · Yuno × Nauta
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-center gap-4 text-[11px] text-slate-400">
            <span className="flex items-center gap-1">
              <Lock className="w-3 h-3 text-emerald-400" /> SD-JWT (RFC 9901)
            </span>
            <span className="text-slate-700">•</span>
            <span className="flex items-center gap-1">
              <Terminal className="w-3 h-3 text-indigo-400" /> JCS (RFC 8785)
            </span>
            <span className="text-slate-700">•</span>
            <span className="flex items-center gap-1">
              <Cpu className="w-3 h-3 text-purple-400" /> AP2 Protocol
            </span>
            <span className="text-slate-700">•</span>
            <span className="flex items-center gap-1 text-slate-300">
              <GitBranch className="w-3 h-3 text-cyan-400" /> Contract v1.1 M2
            </span>
          </div>
        </div>
      </footer>

      {/* Global Toast Notifications */}
      <ToastContainer />
    </div>
  );
};

export const App: React.FC = () => {
  return <AppContent />;
};

export default App;
