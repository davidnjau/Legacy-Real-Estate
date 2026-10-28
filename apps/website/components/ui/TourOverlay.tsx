'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { X, ArrowRight, ArrowLeft, Map } from 'lucide-react';

export type TourStep = {
  /** Matches data-tour-id attribute on the target element */
  targetId: string;
  title: string;
  body: string;
  /** Preferred tooltip position relative to the spotlight */
  placement?: 'right' | 'left' | 'bottom' | 'top';
};

type SpotlightRect = { top: number; left: number; width: number; height: number };

type Props = {
  steps: TourStep[];
  onComplete: () => void;
  onSkip: () => void;
};

const PADDING = 16; // px padding around the spotlight element

export default function TourOverlay({ steps, onComplete, onSkip }: Props) {
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<SpotlightRect | null>(null);
  const [tooltipStyle, setTooltipStyle] = useState<React.CSSProperties>({});
  const [visible, setVisible] = useState(false);
  const animRef = useRef<number | null>(null);
  const current = steps[step];

  // Smoothly update spotlight position on each animation frame
  const updateRect = useCallback(() => {
    const el = document.querySelector(`[data-tour-id="${current.targetId}"]`);
    if (!el) {
      setRect(null);
      return;
    }
    const r = el.getBoundingClientRect();
    setRect({
      top: r.top - PADDING,
      left: r.left - PADDING,
      width: r.width + PADDING * 2,
      height: r.height + PADDING * 2,
    });

    // Tooltip placement
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const TOOLTIP_W = 340;
    const TOOLTIP_H = 200;
    const placement = current.placement || 'right';

    if (placement === 'right' && r.right + TOOLTIP_W + 24 < vw) {
      setTooltipStyle({ top: Math.max(8, r.top + r.height / 2 - TOOLTIP_H / 2), left: r.right + PADDING + 8 });
    } else if (placement === 'left' && r.left - TOOLTIP_W - 24 > 0) {
      setTooltipStyle({ top: Math.max(8, r.top + r.height / 2 - TOOLTIP_H / 2), right: vw - r.left + PADDING + 8 });
    } else if (placement === 'bottom' && r.bottom + TOOLTIP_H + 24 < vh) {
      setTooltipStyle({ top: r.bottom + PADDING + 8, left: Math.max(8, r.left + r.width / 2 - TOOLTIP_W / 2) });
    } else {
      // Default: float to the right of the highlighted area
      setTooltipStyle({
        top: Math.max(8, Math.min(r.top, vh - TOOLTIP_H - 20)),
        left: Math.min(vw - TOOLTIP_W - 20, r.right + PADDING + 8),
      });
    }
  }, [current]);

  useEffect(() => {
    setVisible(false);
    const t = setTimeout(() => {
      updateRect();
      setVisible(true);
    }, 80);
    // Poll to keep in sync with scroll / resize
    animRef.current = window.setInterval(updateRect, 200);
    return () => {
      clearTimeout(t);
      if (animRef.current) clearInterval(animRef.current);
    };
  }, [step, updateRect]);

  // Scroll the target element into view when step changes
  useEffect(() => {
    const el = document.querySelector(`[data-tour-id="${current.targetId}"]`) as HTMLElement | null;
    if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [step, current.targetId]);

  const next = () => {
    if (step < steps.length - 1) setStep(s => s + 1);
    else onComplete();
  };
  const back = () => step > 0 && setStep(s => s - 1);

  // Build the SVG mask path: full screen minus the spotlight cutout
  const maskPath = rect
    ? `M0,0 H${window.innerWidth} V${window.innerHeight} H0Z M${rect.left},${rect.top} H${rect.left + rect.width} V${rect.top + rect.height} H${rect.left}Z`
    : `M0,0 H${window?.innerWidth ?? 1920} V${window?.innerHeight ?? 1080} H0Z`;

  return (
    <div className="fixed inset-0 z-[9999] pointer-events-none">
      {/* Overlay mask with SVG cutout spotlight */}
      <svg
        className="absolute inset-0 w-full h-full pointer-events-auto"
        style={{ transition: 'all 0.3s ease' }}
        onClick={onSkip}
      >
        <defs>
          <mask id="spotlight-mask">
            <rect width="100%" height="100%" fill="white" />
            {rect && (
              <rect
                x={rect.left}
                y={rect.top}
                width={rect.width}
                height={rect.height}
                rx={12}
                fill="black"
              />
            )}
          </mask>
        </defs>
        <rect
          width="100%" height="100%"
          fill="rgba(2,8,23,0.82)"
          mask="url(#spotlight-mask)"
        />
        {/* Glowing border around spotlight */}
        {rect && (
          <rect
            x={rect.left}
            y={rect.top}
            width={rect.width}
            height={rect.height}
            rx={12}
            fill="none"
            stroke="rgba(16,185,129,0.7)"
            strokeWidth={2}
            style={{ filter: 'drop-shadow(0 0 8px rgba(16,185,129,0.5))' }}
          />
        )}
      </svg>

      {/* Tooltip bubble */}
      <div
        className="absolute pointer-events-auto"
        style={{
          ...tooltipStyle,
          width: 340,
          opacity: visible ? 1 : 0,
          transform: visible ? 'scale(1)' : 'scale(0.95)',
          transition: 'opacity 0.25s ease, transform 0.25s ease',
        }}
      >
        <div className="bg-slate-900 border border-emerald-500/30 rounded-2xl shadow-2xl shadow-black/60 overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-5 pt-5 pb-3">
            <div className="flex items-center gap-2">
              <Map size={15} className="text-emerald-400" />
              <span className="text-xs font-semibold text-emerald-400 uppercase tracking-wider">
                Step {step + 1} of {steps.length}
              </span>
            </div>
            <button onClick={onSkip} className="text-slate-500 hover:text-slate-300 transition">
              <X size={16} />
            </button>
          </div>

          {/* Progress bar */}
          <div className="mx-5 h-0.5 bg-white/8 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${((step + 1) / steps.length) * 100}%` }}
            />
          </div>

          {/* Content */}
          <div className="px-5 py-4">
            <h3 className="font-bold text-white text-base mb-2">{current.title}</h3>
            <p className="text-slate-400 text-sm leading-relaxed">{current.body}</p>
          </div>

          {/* Navigation */}
          <div className="flex items-center justify-between px-5 pb-5">
            <button onClick={back} disabled={step === 0}
              className="flex items-center gap-1.5 text-sm font-semibold text-slate-500 hover:text-slate-300 disabled:opacity-30 transition">
              <ArrowLeft size={15} /> Back
            </button>
            <div className="flex items-center gap-3">
              <button onClick={onSkip}
                className="text-xs text-slate-600 hover:text-slate-400 transition">
                Skip tour
              </button>
              <button onClick={next}
                className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-bold px-4 py-2 rounded-xl transition shadow-lg shadow-emerald-900/40">
                {step < steps.length - 1 ? 'Next' : 'Finish'} <ArrowRight size={15} />
              </button>
            </div>
          </div>
        </div>

        {/* Step dots */}
        <div className="flex justify-center gap-1.5 mt-3">
          {steps.map((_, i) => (
            <button key={i} onClick={() => setStep(i)}
              className={`w-1.5 h-1.5 rounded-full transition-all ${
                i === step ? 'bg-emerald-400 w-4' : 'bg-white/20 hover:bg-white/40'
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
