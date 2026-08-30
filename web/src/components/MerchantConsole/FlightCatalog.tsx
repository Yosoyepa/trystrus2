import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { Plane, Edit3, Check, Tag } from 'lucide-react';
import { Badge } from '../common/Badge';
import { Offer } from '../../types';

export const FlightCatalog: React.FC = () => {
  const { offers, updateOfferPrice } = useApp();
  const [editingOfferId, setEditingOfferId] = useState<string | null>(null);
  const [editPrice, setEditPrice] = useState<string>('');

  const handleStartEdit = (offer: Offer) => {
    setEditingOfferId(offer.offer_id);
    setEditPrice(offer.amount);
  };

  const handleSavePrice = async (offerId: string) => {
    if (!editPrice || isNaN(parseFloat(editPrice))) return;
    const formatted = parseFloat(editPrice).toFixed(2);
    await updateOfferPrice(offerId, formatted);
    setEditingOfferId(null);
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-purple-500/10 border border-purple-500/30 text-purple-400">
            <Plane className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                VuelaYa Flight Catalog (Merchant Admin)
              </h3>
              <Badge variant="purple" size="sm">
                Live Price Mutations
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Edit prices dynamically to test price check assertions & background watcher triggers
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {offers.map((offer) => {
          const isEditing = editingOfferId === offer.offer_id;
          const isAdversarial = offer.offer_id === 'ofr_inj_1';
          const isBusiness = offer.offer_id === 'ofr_cor_300';
          const isWatcher = offer.offer_id === 'ofr_watch_118';

          return (
            <div
              key={offer.offer_id}
              className={`p-4 rounded-xl border transition-all flex flex-col justify-between ${
                isAdversarial
                  ? 'bg-rose-950/20 border-rose-500/40 shadow-lg shadow-rose-950/30'
                  : isBusiness
                  ? 'bg-amber-950/20 border-amber-500/40'
                  : isWatcher
                  ? 'bg-cyan-950/20 border-cyan-500/40'
                  : 'bg-[#0e131d] border-slate-800'
              }`}
            >
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-mono text-slate-400 font-semibold flex items-center gap-1.5">
                    <Tag className="w-3 h-3 text-purple-400" /> {offer.flight_num || offer.offer_id}
                  </span>
                  <Badge
                    variant={
                      isAdversarial ? 'rose' : isBusiness ? 'amber' : isWatcher ? 'cyan' : 'purple'
                    }
                    size="sm"
                  >
                    {isAdversarial ? 'Injection Item' : isBusiness ? 'L3 Fare' : isWatcher ? 'Watcher Target' : 'Active'}
                  </Badge>
                </div>

                <div>
                  <h4 className="text-sm font-bold text-slate-100">{offer.title}</h4>
                  <p className="text-[11px] text-slate-400 mt-1 line-clamp-2">{offer.description}</p>
                </div>

                <div className="grid grid-cols-2 gap-2 text-[11px] font-mono pt-1 text-slate-300">
                  <div className="p-2 rounded bg-[#141a27] border border-slate-800/80">
                    <span className="text-slate-500 block text-[9px]">ROUTE</span>
                    {offer.origin} &rarr; {offer.destination}
                  </div>
                  <div className="p-2 rounded bg-[#141a27] border border-slate-800/80">
                    <span className="text-slate-500 block text-[9px]">DATE</span>
                    {offer.date}
                  </div>
                </div>
              </div>

              {/* Price & Edit Section */}
              <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between">
                {isEditing ? (
                  <div className="flex items-center gap-1.5 w-full">
                    <div className="relative flex-1">
                      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400 font-mono text-xs">$</span>
                      <input
                        type="text"
                        value={editPrice}
                        onChange={(e) => setEditPrice(e.target.value)}
                        className="w-full pl-5 pr-2 py-1 rounded-lg bg-[#141a27] border border-indigo-500 text-xs font-mono text-white focus:outline-none"
                        autoFocus
                      />
                    </div>
                    <button
                      onClick={() => handleSavePrice(offer.offer_id)}
                      className="p-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white cursor-pointer"
                      title="Save new price"
                    >
                      <Check className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-baseline gap-1">
                      <span className="text-lg font-bold font-mono text-emerald-400">
                        ${offer.amount}
                      </span>
                      <span className="text-[10px] text-slate-400 font-mono">{offer.currency}</span>
                    </div>

                    <button
                      onClick={() => handleStartEdit(offer)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-[11px] transition-colors cursor-pointer"
                    >
                      <Edit3 className="w-3 h-3" /> Edit Price
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
