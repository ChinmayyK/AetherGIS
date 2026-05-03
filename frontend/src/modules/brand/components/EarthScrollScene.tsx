import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, useMotionValueEvent, useScroll, useTransform } from "motion/react";
import HeroSection from "./sections/HeroSection";
import ProblemSection from "./sections/ProblemSection";
import SystemSection from "./sections/SystemSection";
import DifferentiatorSection from "./sections/DifferentiatorSection";
import DataSourcesSection from "./sections/DataSourcesSection";
import AccessModelSection from "./sections/AccessModelSection";
import UseCasesSection from "./sections/UseCasesSection";
import DashboardTransitionSection from "./sections/DashboardTransitionSection";

interface EarthScrollSceneProps {
  onEnterDashboard?: () => void;
}

const TOTAL_FRAMES = 360;

function getProfile() {
  const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isMobile = typeof window !== "undefined" && window.innerWidth < 900;
  const frameStep = reducedMotion ? 16 : isMobile ? 2 : 1;
  const chunkSize = reducedMotion ? 4 : isMobile ? 8 : 12;
  return { reducedMotion, isMobile, frameStep, chunkSize };
}

const EarthScrollScene: React.FC<EarthScrollSceneProps> = ({ onEnterDashboard }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const framesRef = useRef<HTMLImageElement[]>([]);
  const renderedIdxRef = useRef(-1);
  const rafRef = useRef<number | null>(null);

  const [firstFrameReady, setFirstFrameReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const profile = useMemo(() => getProfile(), []);
  const effectiveTotal = Math.floor((TOTAL_FRAMES - 1) / profile.frameStep) + 1;

  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start start", "end end"],
  });

  const frameFloat = useTransform(scrollYProgress, [0, 1], [0, effectiveTotal - 1]);

  const drawFrame = useCallback((index: number) => {
    const canvas = canvasRef.current;
    const img = framesRef.current[index];
    if (!canvas || !img?.complete || !img.naturalWidth) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));

    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }

    const scale = Math.max(w / img.naturalWidth, h / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;

    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
  }, []);

  useEffect(() => {
    let active = true;

    const loadFrame = (i: number): Promise<HTMLImageElement | null> =>
      new Promise((resolve) => {
        const img = new Image();
        const sourceIndex = Math.min(TOTAL_FRAMES - 1, i * profile.frameStep);
        const name = `frame_${String(sourceIndex).padStart(3, "0")}_delay-0.066s.webp`;
        img.decoding = "async";
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = `/sequence/${name}`;
      });

    const run = async () => {
      const f0 = await loadFrame(0);
      if (!active) return;
      if (f0) {
        framesRef.current[0] = f0;
        setFirstFrameReady(true);
        requestAnimationFrame(() => drawFrame(0));
      }

      for (let i = 1; i < effectiveTotal; i += profile.chunkSize) {
        if (!active) break;
        await Promise.all(
          Array.from({ length: Math.min(profile.chunkSize, effectiveTotal - i) }, (_, j) => {
            const idx = i + j;
            return loadFrame(idx).then((img) => {
              if (!active) return;
              if (img) framesRef.current[idx] = img;
            });
          }),
        );
      }
    };

    run().catch(() => setLoadFailed(true));
    return () => {
      active = false;
    };
  }, [drawFrame, effectiveTotal, profile.chunkSize, profile.frameStep]);

  useMotionValueEvent(frameFloat, "change", (latest) => {
    const idx = Math.round(latest);
    if (idx === renderedIdxRef.current) return;
    if (!framesRef.current[idx]) return;

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      drawFrame(idx);
      renderedIdxRef.current = idx;
    });
  });

  useEffect(() => {
    const onResize = () => {
      const idx = renderedIdxRef.current >= 0 ? renderedIdxRef.current : 0;
      if (framesRef.current[idx]) drawFrame(idx);
    };
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, [drawFrame]);

  return (
    <section ref={containerRef} className="brand-earth-container" aria-label="AetherGIS landing flow">
      <div className="brand-earth-sticky" aria-hidden="true">
        <motion.canvas ref={canvasRef} className="brand-earth-canvas" style={{ opacity: firstFrameReady ? 1 : 0 }} />
        <div className="brand-earth-gradient" />
        {loadFailed && <div className="brand-frame-error-notice">Cinematic frames unavailable.</div>}
      </div>

      <div className="brand-content-flow">
        <HeroSection onEnterDashboard={onEnterDashboard} />
        <ProblemSection />
        <SystemSection />
        <DifferentiatorSection />
        <DataSourcesSection />
        <AccessModelSection />
        <UseCasesSection />
        <DashboardTransitionSection />
      </div>
    </section>
  );
};

export default EarthScrollScene;
