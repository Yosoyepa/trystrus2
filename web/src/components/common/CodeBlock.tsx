import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { clsx } from 'clsx';

interface CodeBlockProps {
  code: unknown;
  language?: string;
  title?: string;
  maxHeight?: string;
  className?: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({
  code,
  language = 'json',
  title,
  maxHeight = 'max-h-72',
  className,
}) => {
  const [copied, setCopied] = useState(false);

  const formattedCode =
    typeof code === 'string'
      ? code
      : JSON.stringify(code, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(formattedCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className={clsx('rounded-lg border border-slate-800 bg-[#0d111a] overflow-hidden text-xs', className)}>
      {(title || language) && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-[#141a27] border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-rose-500/50" />
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500/50" />
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/50" />
            </div>
            {title && <span className="font-mono text-slate-300 font-medium">{title}</span>}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase font-mono text-slate-500">{language}</span>
            <button
              type="button"
              onClick={handleCopy}
              className="p-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors cursor-pointer"
              title="Copy code"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>
      )}
      <div className={clsx('p-3 font-mono overflow-auto leading-relaxed text-slate-300 select-text', maxHeight)}>
        <pre className="whitespace-pre">{formattedCode}</pre>
      </div>
    </div>
  );
};
