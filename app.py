import io
import os
import threading
import time
from pathlib import Path
import tempfile
import warnings
import zipfile
from contextlib import redirect_stdout

import pandas as pd
import streamlit as st

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="Data Rubiks",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def _extraction_gate():
    """One process-wide lock: a single Camelot run can use most of the
    container's RAM/CPU, so concurrent extractions would OOM-restart
    the app for everyone. Later arrivals wait and retry automatically."""
    return threading.Lock()

# ── Design system — sleep-well-creatives: night blues, cream paper, gold ──────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400;1,9..144,500&family=Hanken+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap');

:root {
  --night:      #f6efdf;
  --night-deep: #ebdfc6;
  --navy:       #fdfaf1;
  --blue:       #5468a8;
  --blue-dim:   #9fb0d8;
  --soft:       #76809c;
  --ice:        #232c45;
  --cream:      #273050;
  --cream-2:    #1b2440;
  --gold:       #c09a4a;
  --ink:        #f3edda;
  --serif: 'Fraunces', Georgia, serif;
  --sans:  'Hanken Grotesk', Arial, sans-serif;
  --mono:  'Space Mono', monospace;
}

html, body, [class*="css"], .stApp {
  font-family: var(--sans) !important;
  background:
    radial-gradient(60% 45% at 12% 0%, rgba(192,154,74,0.16), transparent 70%),
    radial-gradient(55% 60% at 82% 38%, rgba(84,104,168,0.10), transparent 70%),
    radial-gradient(120% 90% at 50% 0%, var(--navy) 0%, var(--night) 55%, var(--night-deep) 100%) fixed !important;
  color: var(--cream) !important;
}

#MainMenu, footer, header { visibility: hidden !important; }

.block-container {
  padding: 24px 56px 24px !important;
  max-width: 1500px !important;
}

/* ── top bar ── */
.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 6px 0 18px; border-bottom: 1px solid rgba(35,44,69,0.14);
  margin-bottom: 20px;
}
.logo-name { font-family: var(--serif); font-size: 19px;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--ice); }
.topbar-right {
  margin-left: auto; font-family: var(--mono); font-size: 10.5px;
  letter-spacing: 0.22em; text-transform: uppercase;
  color: rgba(35,44,69,0.55);
}

/* ── hero ── */
.hero { text-align: center; padding: 26px 0 6px; }
.hero-eyebrow {
  display: inline-block;
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.22em;
  text-transform: uppercase; color: var(--soft);
  margin-bottom: 18px;
}
.hero-title {
  font-family: var(--serif); font-weight: 400;
  font-size: clamp(40px, 6vw, 64px); letter-spacing: 0; line-height: 1.08;
  color: var(--ice);
}
.hero-title em { font-style: italic; color: var(--gold); }
.hero-sub {
  margin: 16px auto 0; max-width: 560px;
  color: var(--soft); font-size: 15px; font-weight: 300; line-height: 1.7;
}

/* ── the solver: person + rubik's cube (pure CSS) ── */
.solver-box {
  position: relative; border-radius: 14px;
  border: 1px solid rgba(35,44,69,0.25);
  background: rgba(236,237,242,0.03);
  padding: 44px 64px; margin: 8px 0; min-height: 360px;
  display: flex; align-items: center; justify-content: space-between; gap: 40px;
}
.scan-left { position: relative; text-align: left; max-width: 400px; }
.scan-title { font-family: var(--serif); font-size: 28px; color: var(--ice); }
.scan-msg {
  font-family: var(--mono); font-size: 12px;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--gold);
  margin-top: 12px; min-height: 14px;
}
.scan-msg::after { content: '▍'; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.scan-sub { color: var(--soft); font-size: 12px; margin-top: 6px; font-family: var(--mono); letter-spacing: 0.08em; }
.scan-bar {
  position: relative; height: 3px; border-radius: 2px;
  background: rgba(35,44,69,0.25);
  margin: 16px 0 0; max-width: 660px; overflow: hidden;
}
.scan-bar > div {
  height: 100%; background: var(--gold); border-radius: 2px;
  transition: width .25s ease;
}
.scan-bar.indet > div {
  width: 35% !important; animation: slide 1.2s ease-in-out infinite alternate;
}
@keyframes slide { from { margin-left: 0; } to { margin-left: 65%; } }

.solver { position: relative; width: 280px; height: 240px; flex-shrink: 0; }
.person { position: absolute; inset: 0; width: 100%; height: auto; opacity: 0.9; }
.arm { transform-origin: 120px 84px; }
.arm-l { animation: arm-wiggle 2.4s ease-in-out infinite; }
.arm-r { animation: arm-wiggle 2.4s ease-in-out infinite reverse; }
@keyframes arm-wiggle { 0%,100% { transform: rotate(0deg); } 50% { transform: rotate(4deg); } }

.cube-stage {
  position: absolute; left: 50%; top: 96px;
  width: 126px; height: 126px; margin-left: -63px;
  perspective: 1100px;
}
.cube-rotor {
  position: absolute; inset: 0; transform-style: preserve-3d;
  animation: cube-spin 9s linear infinite;
}
@keyframes cube-spin {
  0%   { transform: rotateX(-24deg) rotateY(0deg)   rotateZ(0deg); }
  25%  { transform: rotateX(-42deg) rotateY(90deg)  rotateZ(7deg); }
  50%  { transform: rotateX(-8deg)  rotateY(180deg) rotateZ(-7deg); }
  75%  { transform: rotateX(-40deg) rotateY(270deg) rotateZ(6deg); }
  100% { transform: rotateX(-24deg) rotateY(360deg) rotateZ(0deg); }
}
.slice {
  position: absolute; left: 0; width: 126px; height: 42px;
  transform-style: preserve-3d;
}
.slice-top { top: 0;    animation: twist-a 7.2s cubic-bezier(.7,0,.2,1) infinite; }
.slice-mid { top: 42px; animation: twist-b 7.2s cubic-bezier(.7,0,.2,1) infinite; }
.slice-bot { top: 84px; animation: twist-c 7.2s cubic-bezier(.7,0,.2,1) infinite; }
/* sequential layer moves, like solving */
@keyframes twist-a { 0%,8% {transform:rotateY(0)} 16%,100% {transform:rotateY(90deg)} }
@keyframes twist-b { 0%,36% {transform:rotateY(0)} 46%,100% {transform:rotateY(-90deg)} }
@keyframes twist-c { 0%,66% {transform:rotateY(0)} 76%,100% {transform:rotateY(90deg)} }
.slice-face {
  position: absolute; width: 126px; height: 42px;
  display: flex; flex-direction: column;
  background: #2c3142;
}
.sf-0 { transform: rotateY(0deg)   translateZ(63px); }
.sf-1 { transform: rotateY(90deg)  translateZ(63px); }
.sf-2 { transform: rotateY(180deg) translateZ(63px); }
.sf-3 { transform: rotateY(270deg) translateZ(63px); }
.sf-up { width: 126px; height: 126px; transform: rotateX(90deg) translateZ(63px); }
.sticker-row { display: flex; flex: 1; gap: 2px; padding: 1px; height: 100%; }
.sf-up .sticker-row { height: 33.3%; }
.sticker { flex: 1; border-radius: 3px; display: block; }
.cube-shadow {
  position: absolute; left: 50%; top: 250px; width: 130px; height: 18px;
  margin-left: -55px; border-radius: 50%;
  background: radial-gradient(ellipse, rgba(35,44,69,0.30) 0%, transparent 70%);
}

/* ── book spread (results) ── */
.book {
  position: relative; display: flex; border-radius: 10px;
  background: var(--night-deep); padding: 12px; margin: 4px 0 18px;
  box-shadow: 0 30px 60px rgba(35,44,69,0.5), inset 0 0 0 1px rgba(35,44,69,0.18);
}
.bpage {
  background: linear-gradient(135deg, var(--cream) 0%, var(--cream-2) 100%);
  color: var(--ink); padding: 30px 34px; flex: 1;
}
.bpage-l { border-radius: 6px 0 0 6px; border-right: 1px solid rgba(243,237,222,0.18); }
.bpage-r { border-radius: 0 6px 6px 0; }
.bk-kicker {
  font-family: var(--mono); font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.22em; color: var(--blue-dim);
}
.bk-title {
  font-family: var(--serif); font-style: italic; font-weight: 400;
  font-size: 44px; line-height: 1; margin: 10px 0 8px; color: var(--ink);
}
.bk-sub { font-size: 13px; font-weight: 300; color: rgba(243,237,222,0.7); max-width: 320px; }
.bk-stats { display: flex; gap: 30px; margin-top: 8px; }
.bk-stat .v { font-family: var(--serif); font-size: 38px; line-height: 1.1; color: var(--ink); }
.bk-stat .v.good { color: var(--blue-dim); }
.bk-stat .v.bad { color: #e0a36a; }
.bk-stat .k {
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.16em;
  text-transform: uppercase; color: rgba(243,237,222,0.6); margin-top: 2px;
}

/* ── widgets, recoloured to the palette ── */
.stTextInput input, .stSelectbox div[data-baseweb] {
  background: rgba(236,237,242,0.06) !important;
  border-color: rgba(35,44,69,0.35) !important;
  color: var(--ice) !important; font-family: var(--mono) !important; font-size: 12px !important;
}
.stDownloadButton { margin-top: 10px; }
.stDownloadButton button, .stButton button {
  background: var(--cream) !important; color: var(--ink) !important;
  border: 1px solid var(--cream) !important; border-radius: 9px !important;
  font-family: var(--sans) !important; font-weight: 500 !important; font-size: 13px !important;
  letter-spacing: 0.02em !important; padding: 12px 20px !important;
  font-size: 14.5px !important;
  transition: transform .15s ease !important;
}
.stDownloadButton button:active, .stButton button:active {
  transform: scale(0.98) !important;
}
.stDownloadButton button:hover, .stButton button:hover {
  background: var(--gold) !important; border-color: var(--gold) !important;
}
div[data-testid="stFileUploader"] {
  border: 1px dashed rgba(242,234,216,0.35); border-radius: 14px;
  background: rgba(236,237,242,0.03); padding: 6px;
}
div[data-testid="stFileUploader"]:hover { border-color: var(--gold); }
div[data-testid="stFileUploader"] section { background: transparent !important; }
div[data-testid="stFileUploader"] section span,
div[data-testid="stFileUploader"] section small,
div[data-testid="stFileUploader"] section div {
  color: rgba(35,44,69,0.75) !important; font-size: 15px !important;
}
div[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"],
div[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] * {
  color: #f7f2e4 !important; font-size: 14px !important;
}
div[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] small {
  color: rgba(247,242,228,0.7) !important;
}
div[data-testid="stFileUploader"] section button,
div[data-testid="stFileUploader"] section button * {
  color: #f7f2e4 !important; fill: #f7f2e4 !important;
  font-size: 15px !important; font-weight: 500 !important;
}
div[data-testid="stFileUploader"] section button {
  background: var(--cream) !important; color: var(--ink) !important; border-radius: 6px !important;
  font-weight: 600 !important;
}
div[data-testid="stDataFrame"] {
  border: 1px solid rgba(35,44,69,0.25); border-radius: 8px;
}
.stTabs { margin-top: 16px; }
.stTabs [data-baseweb="tab-list"] {
  gap: 10px; background: transparent; border-bottom: none !important;
}
.stTabs [data-baseweb="tab"] {
  font-family: var(--sans) !important; font-size: 13.5px !important;
  font-weight: 500 !important; letter-spacing: 0.04em;
  text-transform: uppercase; color: rgba(35,44,69,0.65) !important;
  background: rgba(255,255,255,0.38) !important;
  backdrop-filter: blur(10px) !important;
  -webkit-backdrop-filter: blur(10px) !important;
  border: 1px solid rgba(35,44,69,0.16) !important;
  border-radius: 999px !important;
  padding: 9px 24px !important;
  box-shadow: 0 2px 10px rgba(35,44,69,0.05) !important;
  transition: background .2s, color .2s, border-color .2s !important;
}
.stTabs [data-baseweb="tab"]:hover {
  background: rgba(255,255,255,0.65) !important;
  border-color: rgba(192,154,74,0.5) !important;
}
.stTabs [aria-selected="true"] {
  color: #f3edda !important;
  background: rgba(39,48,80,0.92) !important;
  border: 1px solid rgba(39,48,80,0.92) !important;
  box-shadow: 0 4px 16px rgba(35,44,69,0.22) !important;
}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { display: none !important; }
hr { border-color: rgba(35,44,69,0.25) !important; margin: 10px 0 !important; }
.note {
  border-top: 1px solid rgba(35,44,69,0.3);
  padding: 14px 0 0; color: rgba(35,44,69,0.85); font-size: 14.5px;
  font-weight: 400; line-height: 1.8; max-width: 660px; margin: 16px 0 0 !important;
}
.note strong { color: var(--ice); font-weight: 500; }

/* ── upload hero: sleep-well first page ── */
.night {
  position: relative; width: 100%; height: 560px; margin: 0 auto;
  overflow: hidden; border-radius: 0 0 18px 18px;
}
.star { position: absolute; width: 3px; height: 3px; border-radius: 50%;
  background: var(--gold); animation: twinkle 3s ease-in-out infinite; }
@keyframes twinkle { 0%,100% { opacity: .1; } 50% { opacity: .95; } }
.aurora {
  position: absolute; left: -10%; right: -10%; top: 270px; height: 170px;
  background:
    radial-gradient(50% 90% at 30% 50%, rgba(35,44,69,0.30), transparent 70%),
    radial-gradient(45% 80% at 70% 40%, rgba(37,117,246,0.28), transparent 70%),
    radial-gradient(60% 100% at 50% 60%, rgba(236,237,242,0.14), transparent 75%);
  filter: blur(14px);
  animation: aurora-drift 9s ease-in-out infinite alternate;
}
@keyframes aurora-drift {
  from { transform: translateX(-26px) scaleY(1); }
  to   { transform: translateX(26px) scaleY(1.18); }
}
.moon-emblem {
  position: absolute; left: 50%; top: 18px; transform: translateX(-50%);
  width: 44px; height: 58px; border: 2px solid var(--cream);
  border-radius: 50% / 42%;
  display: flex; align-items: center; justify-content: center;
  box-shadow: inset 0 0 0 3px var(--night), inset 0 0 0 4px rgba(242,234,216,0.6);
}
.moon-emblem span { color: var(--cream); font-size: 18px; line-height: 1;
  transform: rotate(-22deg); }
.night-eyebrow {
  position: absolute; top: 118px; left: 0; right: 0; text-align: center;
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.3em;
  text-transform: uppercase; color: var(--ice);
}
.night-eyebrow b { color: var(--soft); font-weight: 400; padding: 0 14px; }
.night-title {
  position: absolute; top: 148px; left: 0; right: 0; text-align: center;
  font-family: var(--serif); font-weight: 400;
  font-size: 76px; letter-spacing: 0.30em; text-indent: 0.30em;
  color: var(--ice); line-height: 1.1;
  text-shadow: 0 0 40px rgba(236,237,242,0.25);
  animation: title-in 2.2s ease-out backwards;
}
@keyframes title-in { from { opacity: 0; letter-spacing: 0.5em; }
  to { opacity: 1; letter-spacing: 0.30em; } }
.night-title em { font-style: italic; color: var(--gold); }
.dunes { position: absolute; left: 0; right: 0; bottom: 0; height: 240px; }
.dunes svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.figure {
  position: absolute; left: 50%; bottom: 116px; width: 46px; height: 110px;
  transform: translateX(-50%);
}
.scroll-hint {
  position: absolute; left: 0; right: 0; bottom: 14px; text-align: center;
  font-family: var(--sans); font-size: 12px; color: var(--blue);
  letter-spacing: 0.02em;
}
.grain { position: absolute; inset: 0; opacity: 0.5; pointer-events: none;
  mix-blend-mode: overlay; }

/* ── single-page layout: heading + dropzone left, cube right ── */
.hero-left { padding: 7vh 0 8px; text-align: left; }
.hl-eyebrow {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.28em;
  text-transform: uppercase; color: var(--ice);
}
.hl-eyebrow b { color: var(--soft); font-weight: 400;
  animation: eyeflash 2s ease-in-out infinite; }
@keyframes eyeflash { 0%,100% { opacity: .2; } 50% { opacity: 1; } }
.hl-title {
  font-family: var(--serif); font-weight: 700; font-size: 54px;
  letter-spacing: 0.30em; line-height: 1.15; color: var(--ice);
  text-transform: uppercase;
  margin-top: 12px; text-shadow: 0 0 50px rgba(236,237,242,0.25);
  animation: hl-in 1.6s ease-out backwards;
}
@keyframes hl-in { from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); } }
.hl-title em { font-style: normal; color: var(--ice); }
.hl-rule {
  width: 64px; height: 2px; background: var(--gold);
  margin: 16px 0 14px; border-radius: 1px;
  box-shadow: 0 0 12px rgba(192,154,74,0.45);
}
.hl-sub {
  font-family: var(--serif); font-style: italic; font-size: 21px;
  color: rgba(35,44,69,0.78); margin: 0 0 34px;
  animation: hl-in 1.6s ease-out .4s backwards;
}
div[data-testid="stFileUploader"] {
  border: 1px dashed rgba(236,237,242,0.28) !important;
  border-radius: 12px !important;
  background: rgba(35,44,69,0.35) !important;
  max-width: 660px !important;
  transition: border-color .25s ease;
}
div[data-testid="stFileUploader"]:hover {
  border-color: rgba(230,195,92,0.65) !important;
}
.note { border-top: none !important; padding-top: 8px; }
.feat-row {
  display: flex; gap: 14px; margin-top: 26px; max-width: 660px;
}
.feat {
  flex: 1; padding: 18px 20px; border-radius: 22px;
  background: rgba(255,255,255,0.42);
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(35,44,69,0.14);
  box-shadow: 0 4px 18px rgba(35,44,69,0.07);
  transition: transform .2s ease, border-color .2s ease;
}
.feat:hover { transform: translateY(-3px); border-color: rgba(192,154,74,0.55); }
.feat-n {
  font-family: var(--serif); font-style: italic; font-size: 20px;
  color: var(--gold);
}
.feat-t {
  font-family: var(--sans); font-weight: 600; font-size: 14.5px;
  color: var(--ice); margin-top: 6px;
}
.feat-d {
  font-size: 12.5px; color: rgba(35,44,69,0.65); line-height: 1.55;
  margin-top: 5px;
}
.status-left { padding: 10px 2px 0; }
.scan-row { display: flex; align-items: baseline; justify-content: space-between;
  max-width: 660px; }
.scan-pct {
  font-family: var(--serif); font-size: 30px; color: var(--gold); line-height: 1;
}

.cube-pane { position: relative; height: 520px; margin-top: 9vh; }
.cube-pane .cube-stage { top: 150px; transform: scale(1.95); }
.cube-pane .cube-shadow { top: 360px; transform: scale(2.9); }
.cube-idle .slice { animation: none !important; }
.cube-idle .cube-rotor { animation-duration: 18s; }

/* ── morph: monitor collapses into the cube ── */
.ghost-monitor {
  position: absolute; left: 50%; top: 70px; width: 220px; height: 150px;
  margin-left: -110px; border: 3px solid var(--cream); border-radius: 10px;
  animation: collapse 1.5s cubic-bezier(.6,-0.1,.3,1) forwards;
}
.ghost-monitor::after { content: ''; position: absolute; left: 50%; bottom: -26px;
  width: 10px; height: 26px; margin-left: -5px; background: var(--cream); }
@keyframes collapse {
  0%   { transform: scale(1) rotate(0deg); opacity: .9; }
  70%  { transform: scale(.28) rotate(200deg); opacity: .7; }
  100% { transform: scale(.06) rotate(360deg); opacity: 0; }
}
.morph-in .cube-stage { animation: cube-arrive 1s ease-out .9s backwards; }
@keyframes cube-arrive { from { transform: scale(0) ; } to { transform: scale(1); } }

/* ── results pop-in ── */
.pop { animation: pop-in .6s cubic-bezier(.2,1.4,.4,1) backwards; }
.pop-2 { animation-delay: .15s; } .pop-3 { animation-delay: .3s; }
@keyframes pop-in { from { transform: scale(.7) translateY(16px); opacity: 0; }
  to { transform: scale(1) translateY(0); opacity: 1; } }

/* ════════ responsive: tablet (≤1100px) ════════ */
@media (max-width: 1100px) {
  .block-container { padding: 18px 28px 24px !important; }
  .hl-title { font-size: clamp(34px, 6vw, 46px); letter-spacing: 0.22em; }
  .hero-left { padding-top: 4vh; }
  .cube-pane { height: 420px; margin-top: 5vh; }
  .cube-pane .cube-stage { top: 120px; transform: scale(1.45); }
  .cube-pane .cube-shadow { top: 300px; transform: scale(2.2); }
  .bk-title { font-size: 36px; }
  .bk-stats { gap: 22px; }
  .solver-box { padding: 32px 36px; gap: 24px; }
}

/* ════════ responsive: phone (≤700px) ════════ */
@media (max-width: 700px) {
  .block-container { padding: 12px 14px 20px !important; }

  .topbar { padding-bottom: 12px; margin-bottom: 12px; }
  .topbar-right { display: none; }
  .logo-name { font-size: 16px; }

  .hero-left { padding: 2vh 0 4px; text-align: center; }
  .hl-title {
    font-size: clamp(26px, 8.5vw, 36px);
    letter-spacing: 0.14em; text-indent: 0.14em;
  }
  .hl-rule { margin: 14px auto 12px; }
  .hl-sub { font-size: 16px; margin-bottom: 20px; }
  .hl-eyebrow { letter-spacing: 0.18em; }

  /* feature cards stack */
  .feat-row { flex-direction: column; gap: 10px; margin-top: 18px; }
  .feat { padding: 14px 16px; border-radius: 16px; }

  /* cube pane shrinks below the stacked upload column */
  .cube-pane { height: 260px; margin-top: 0; }
  .cube-pane .cube-stage { top: 60px; transform: scale(1.0); }
  .cube-pane .cube-shadow { top: 210px; transform: scale(1.4); }

  /* solver box stacks: text above, cube below */
  .solver-box {
    flex-direction: column; align-items: flex-start;
    padding: 22px 18px; min-height: unset; gap: 8px;
  }
  .solver { width: 100%; height: 200px; transform: scale(0.8); transform-origin: top center; }
  .scan-left { max-width: 100%; }
  .scan-title { font-size: 22px; }
  .scan-pct { font-size: 24px; }

  /* book spread stacks into single pages */
  .book { flex-direction: column; padding: 8px; }
  .bpage { padding: 20px 18px; }
  .bpage-l { border-radius: 6px 6px 0 0; border-right: none;
    border-bottom: 1px solid rgba(243,237,222,0.18); }
  .bpage-r { border-radius: 0 0 6px 6px; }
  .bk-title { font-size: 30px; }
  .bk-sub { max-width: 100%; }
  .bk-stats { gap: 16px; flex-wrap: wrap; }
  .bk-stat .v { font-size: 28px; }

  /* pill tabs: scroll instead of wrap-overflow */
  .stTabs [data-baseweb="tab-list"] {
    gap: 6px; overflow-x: auto !important; flex-wrap: nowrap !important;
    -webkit-overflow-scrolling: touch;
  }
  .stTabs [data-baseweb="tab"] {
    padding: 7px 14px !important; font-size: 12px !important;
    white-space: nowrap !important;
  }

  .note { font-size: 13px; line-height: 1.6; }
  .night { height: 420px; }
  .night-title { font-size: clamp(34px, 10vw, 52px); letter-spacing: 0.18em; text-indent: 0.18em; }

  /* download buttons full width, comfortable tap targets */
  .stDownloadButton button, .stButton button {
    width: 100% !important; padding: 13px 16px !important;
  }
}

/* ════════ responsive: small phone (≤400px) ════════ */
@media (max-width: 400px) {
  .hl-title { font-size: 24px; letter-spacing: 0.1em; }
  .bk-stats { gap: 12px; }
  .bk-stat .v { font-size: 24px; }
}
</style>
""", unsafe_allow_html=True)

# ── Top bar ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="topbar">
  <div class="logo-name">Data Rubiks</div>
  <div class="topbar-right">PDF &rarr; clean tables</div>
</div>
""", unsafe_allow_html=True)


STICKERS = ["#D9B25F", "#F4F1E8", "#D08B4C", "#6F9573", "#5B74A8", "#B0524A"]


def _sticker_row(seed):
    cells = "".join(
        f'<span class="sticker" style="background:{STICKERS[(seed + c * 2) % 6]}"></span>'
        for c in range(3)
    )
    return f'<div class="sticker-row">{cells}</div>'


def _slice(pos):
    faces = "".join(
        f'<div class="slice-face sf-{f}">{_sticker_row(f + len(pos))}</div>'
        for f in range(4)
    )
    up = ""
    if pos == "top":
        up = (
            '<div class="slice-face sf-up">'
            + _sticker_row(1) + _sticker_row(3) + _sticker_row(5)
            + "</div>"
        )
    return f'<div class="slice slice-{pos}">{faces}{up}</div>'


def _cube_solver():
    person = """
    <svg class="person" viewBox="0 0 240 200" fill="none">
      <circle cx="120" cy="48" r="22" stroke="#94B8F2" stroke-width="4"/>
      <path d="M120 70 C 120 100, 118 112, 116 128" stroke="#94B8F2" stroke-width="4" stroke-linecap="round"/>
      <path d="M116 128 C 90 150, 70 156, 48 152 C 80 168, 160 168, 192 152 C 170 156, 142 150, 116 128"
        stroke="#94B8F2" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path class="arm arm-l" d="M118 84 C 96 92, 84 100, 78 112" stroke="#FEF1D0" stroke-width="4" stroke-linecap="round"/>
      <path class="arm arm-r" d="M122 84 C 144 92, 156 100, 162 112" stroke="#FEF1D0" stroke-width="4" stroke-linecap="round"/>
    </svg>"""
    cube = (
        '<div class="cube-stage"><div class="cube-rotor">'
        + _slice("top") + _slice("mid") + _slice("bot")
        + "</div></div>"
    )
    return f'<div class="solver">{person}{cube}<div class="cube-shadow"></div></div>'


HERO_GSAP = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;1,9..144,400&family=Space+Mono&display=swap');
html,body{margin:0;overflow:hidden;height:100%;
  background:radial-gradient(120% 100% at 50% 0%,#002e77 0%,#121a30 55%,#0a0f1f 100%)}
#sky{position:absolute;inset:0;overflow:hidden}
.s{position:absolute;border-radius:50%;background:#f5edda;
  box-shadow:0 0 6px rgba(242,234,216,.9)}
.s.g{background:#c09a4a;box-shadow:0 0 8px rgba(230,195,92,.9)}
.ov{position:absolute;left:0;right:0;text-align:center;pointer-events:none;z-index:2}
#eye{top:96px;font-family:'Space Mono',monospace;font-size:11px;
  letter-spacing:.3em;text-transform:uppercase;color:#f2f4fb}
#eye b{color:#76809c;font-weight:400;padding:0 12px}
#title{top:126px;font-family:'Fraunces',Georgia,serif;font-size:60px;
  color:#f2f4fb;letter-spacing:.22em;text-indent:.22em;
  text-shadow:0 0 50px rgba(236,237,242,.3)}
#tsub{top:206px;font-family:'Fraunces',Georgia,serif;font-style:italic;
  font-size:19px;color:#76809c;letter-spacing:.02em}
#title em{font-style:italic;color:#c09a4a}

</style>
<div id="sky"></div>
<div class="ov" id="eye"><b>&#9679;</b>GOVERNMENT PDF &rarr; CLEAN DATA<b>&#9679;</b></div>
<div class="ov" id="title">DATA<em>GEN</em></div>
<div class="ov" id="tsub">Your assistant for extracting data tables from any PDF.</div>
<div class="ov" id="hint">&#8964; drop a pdf to begin</div>
__GSAP_JS__
<script>
const sky=document.getElementById('sky'),W=innerWidth,H=560;
for(let i=0;i<70;i++){
  const s=document.createElement('div');
  s.className='s'+(Math.random()<.4?' g':'');
  const sz=1.5+Math.random()*2.5;
  s.style.width=s.style.height=sz+'px';
  s.style.left=Math.random()*100+'%';
  s.style.top='-20px';
  sky.appendChild(s);
  gsap.to(s,{y:H+60,x:'+='+(Math.random()*80-40),duration:3+Math.random()*6,
    delay:Math.random()*8,repeat:-1,ease:'none'});
  gsap.to(s,{opacity:.15+Math.random()*.3,duration:.6+Math.random(),
    repeat:-1,yoyo:true,ease:'sine.inOut'});
}
gsap.from('#title',{opacity:0,letterSpacing:'.4em',duration:2.2,ease:'power3.out'});
gsap.from('#tsub',{opacity:0,y:10,duration:1.6,delay:.7,ease:'power2.out'});
gsap.from('#eye',{opacity:0,y:-12,duration:1.4,delay:.4,ease:'power2.out'});
gsap.to('#eye b',{opacity:.15,duration:1,repeat:-1,yoyo:true,ease:'sine.inOut'});
gsap.to('#hint',{y:5,duration:1.1,repeat:-1,yoyo:true,ease:'sine.inOut'});
</script>
"""

INTERACTIVE_CUBE = """
<style>
html,body{margin:0;background:transparent;overflow:hidden}
#wrap{width:100%;height:500px;display:flex;flex-direction:column;align-items:center;
  justify-content:center;cursor:grab;user-select:none;-webkit-user-select:none;
  touch-action:none}
#wrap:active{cursor:grabbing}
#persp{perspective:1100px;transform:scale(1.45)}
@media (max-width:1100px){#persp{transform:scale(1.15)}}
@media (max-width:700px){
  #wrap{height:340px;justify-content:flex-start;padding-top:30px}
  #persp{transform:scale(0.9)}
}
#cube{position:relative;width:150px;height:150px;transform-style:preserve-3d}
.cb{position:absolute;width:48px;height:48px;transform-style:preserve-3d}
.f{position:absolute;width:46px;height:46px;border-radius:5px;border:1px solid #2c3142}





</style>
<div id="wrap">
  <div id="persp"><div id="cube"></div></div>
</div>
<script>
const COLS={F:'#6F9573',B:'#5B74A8',R:'#B0524A',L:'#D08B4C',U:'#F4F1E8',D:'#D9B25F'};
const SCRAMBLE=__SCRAMBLE__,PAL=Object.values(COLS),PLAYLABEL='__PLAYBTN__';
const cube=document.getElementById('cube'),S=50;
for(let x=-1;x<=1;x++)for(let y=-1;y<=1;y++)for(let z=-1;z<=1;z++){
  const c=document.createElement('div');c.className='cb';
  c.style.transform=`translate3d(${x*S+51}px,${y*S+51}px,${z*S}px)`;
  const faces=[];
  if(z===1)faces.push(['rotateY(0deg)',COLS.F]);
  if(z===-1)faces.push(['rotateY(180deg)',COLS.B]);
  if(x===1)faces.push(['rotateY(90deg)',COLS.R]);
  if(x===-1)faces.push(['rotateY(-90deg)',COLS.L]);
  if(y===-1)faces.push(['rotateX(90deg)',COLS.U]);
  if(y===1)faces.push(['rotateX(-90deg)',COLS.D]);
  for(const[r,col]of faces){
    const f=document.createElement('div');f.className='f';
    f.style.background=SCRAMBLE?PAL[Math.floor(Math.random()*6)]:col;
    f.style.transform=`${r} translateZ(24px)`;
    c.appendChild(f);
  }
  cube.appendChild(c);
}
let rx=-26,ry=-38,rz=0,t0=0,dragging=false,px=0,py=0,idle=true;
function render(){cube.style.transform=`rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${rz}deg)`;}
render();
const wrap=document.getElementById('wrap');
wrap.addEventListener('pointerdown',e=>{dragging=true;idle=false;px=e.clientX;py=e.clientY;});
window.addEventListener('pointermove',e=>{
  if(!dragging)return;
  ry+=(e.clientX-px)*0.5;rx-=(e.clientY-py)*0.5;
  rx=Math.max(-90,Math.min(90,rx));
  px=e.clientX;py=e.clientY;render();
});
window.addEventListener('pointerup',()=>{dragging=false;
  setTimeout(()=>{idle=true},2500);});
(function spin(){
  if(idle&&!dragging&&!handMode){
    t0+=0.012;
    ry+=0.35;
    rx=-24+20*Math.sin(t0*1.1);
    rz=10*Math.sin(t0*0.6);
    render();
  }
  requestAnimationFrame(spin);})();

const handMode=false;
</script>
"""


def interactive_cube(hint, play_label, scramble):
    return (
        INTERACTIVE_CUBE
        .replace("__HINT__", hint)
        .replace("__PLAYBTN__", play_label)  # legacy, markup removed
        .replace("__SCRAMBLE__", "true" if scramble else "false")
    )


def cube_pane(mode="solving", caption=""):
    cls = "cube-idle" if mode == "idle" else ""
    cube = (
        '<div class="cube-stage"><div class="cube-rotor">'
        + _slice("top") + _slice("mid") + _slice("bot")
        + "</div></div>"
    )
    return (
        f'<div class="cube-pane {cls}">{cube}'
        f'<div class="cube-shadow"></div></div>'
    )


def status_anim(msg, sub, pct_prev, pct, eta=""):
    """GSAP-animated progress: counts the percent up smoothly and
    tweens the bar between updates; shows estimated time remaining."""
    eta_html = f" &nbsp;&middot;&nbsp; ~{eta} left" if eta else ""
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Space+Mono&display=swap');
html,body{{margin:0;background:transparent;overflow:hidden}}
#wrap{{max-width:660px;font-family:'Space Mono',monospace}}
#row{{display:flex;align-items:baseline;justify-content:space-between}}
#msg{{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#c09a4a}}
#msg::after{{content:'\258d';animation:bl 1s steps(1) infinite}}
@keyframes bl{{50%{{opacity:0}}}}
#pct{{font-family:'Fraunces',Georgia,serif;font-size:40px;color:#c09a4a;line-height:1}}
#sub{{color:#5d6781;font-size:12.5px;margin-top:6px;letter-spacing:.08em}}
#bar{{height:3px;border-radius:2px;background:rgba(35,44,69,.25);margin-top:14px;overflow:hidden}}
#fill{{height:100%;border-radius:2px;background:#c09a4a;width:{pct_prev:.1f}%}}
</style>
<div id="wrap">
  <div id="row"><span id="msg">{msg}</span><span id="pct">{pct_prev:.0f}%</span></div>
  <div id="sub">{sub}{eta_html}</div>
  <div id="bar"><div id="fill"></div></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<script>
const el=document.getElementById('pct'),fill=document.getElementById('fill');
if(window.gsap){{
  const o={{v:{pct_prev:.1f}}};
  gsap.to(o,{{v:{pct:.1f},duration:.5,ease:'power1.out',
    onUpdate:()=>{{el.textContent=Math.round(o.v)+'%';}}}});
  gsap.to(fill,{{width:'{pct:.1f}%',duration:.5,ease:'power1.out'}});
}}else{{
  el.textContent='{pct:.0f}%';fill.style.width='{pct:.1f}%';
}}
</script>"""


def _nice_name(filename):
    """Shorten unwieldy upload names: keep the meaningful tail."""
    stem = filename.rsplit(".", 1)[0]
    if len(stem) > 42:
        stem = "…" + stem[-40:]
    return stem + ".pdf"


def _fmt_eta(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


def status_html(msg, sub="", pct=None):
    bar_cls = "scan-bar" if pct is not None else "scan-bar indet"
    width = f"{pct:.0f}%" if pct is not None else "35%"
    pct_label = (
        f'<span class="scan-pct">{pct:.0f}%</span>'
        if pct is not None else
        '<span class="scan-pct">&hellip;</span>'
    )
    return f"""
    <div class="status-left">
      <div class="scan-row"><div class="scan-msg">{msg}</div>{pct_label}</div>
      <div class="scan-sub">{sub}</div>
      <div class="{bar_cls}"><div style="width:{width}"></div></div>
    </div>"""


# ── Main page: heading + dropzone left, rubik's cube right ────────────────────
left, right = st.columns([1.3, 1], gap="large")

with right:
    cube_ph = st.empty()

with left:
    st.markdown("""
    <div class="hero-left">
      <div class="hl-title">DATA RUBIKS</div>
      <div class="hl-rule"></div>
      <div class="hl-sub">Your assistant for extracting data tables from any PDF.</div>
    </div>
    """, unsafe_allow_html=True)
    uploaded = st.file_uploader("pdf", type=["pdf"], label_visibility="collapsed")
    status_ph = st.empty()

if uploaded is None:
    cube_ph.markdown(
        cube_pane("idle"),
        unsafe_allow_html=True,
    )
    status_ph.markdown("""
    <div class="feat-row">
      <div class="feat">
        <span class="feat-n">#1</span>
        <div class="feat-t">650+ pages, one go</div>
        <div class="feat-d">Handles full statistical reports &mdash; every bordered or borderless table.</div>
      </div>
      <div class="feat">
        <span class="feat-n">#2</span>
        <div class="feat-t">Hindi &rarr; English</div>
        <div class="feat-d">Detects Kruti Dev &amp; Devanagari scripts and translates tables automatically.</div>
      </div>
      <div class="feat">
        <span class="feat-n">#3</span>
        <div class="feat-t">Excel &amp; CSV, ready</div>
        <div class="feat-d">Multi-page tables stitched, named and bundled into clean workbooks.</div>
      </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

cube_ph.markdown(cube_pane("solving"), unsafe_allow_html=True)

# ── Run pipeline ───────────────────────────────────────────────────────────────
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp.write(uploaded.getvalue())
    pdf_path = tmp.name

_gate = _extraction_gate()
_gate_held = False

try:
    if "results" not in st.session_state or st.session_state.get("pdf_name") != uploaded.name:

        _gate_held = _gate.acquire(blocking=False)

        if not _gate_held:
            status_ph.markdown(
                status_html("in queue",
                            "another extraction is running — yours starts automatically"),
                unsafe_allow_html=True,
            )
            time.sleep(6)
            st.rerun()

        from pypdf import PdfReader as _PdfReader
        _n_pages = len(_PdfReader(pdf_path).pages)
        # ~1.2 s/page empirical (camelot lattice+stream + cleaning)
        _est_str = _fmt_eta(int(_n_pages * 1.2))

        status_ph.markdown(
            status_html(
                "reading the document",
                f"{_nice_name(uploaded.name)} &nbsp;·&nbsp; "
                f"{_n_pages} page{'s' if _n_pages != 1 else ''} &nbsp;·&nbsp; "
                f"~{_est_str} estimated",
            ),
            unsafe_allow_html=True,
        )

        from backend.app.extract.table_extractor import extract_tables
        tables = extract_tables(pdf_path)

        if not tables:
            st.error("No tables found in this PDF.")
            st.stop()

        from backend.app.cleaning.header_builder import apply_headers
        from backend.app.cleaning.header_detector import detect_header_rows
        from backend.app.cleaning.header_postprocessor import clean_headers
        from backend.app.cleaning.universal_cleaner import clean_dataframe
        from backend.app.cleaning.wrapped_row_reassembler import reassemble_wrapped_rows
        from backend.app.export.excel_exporter import build_workbook
        from backend.app.standardization.metadata_builder import build_metadata
        from backend.app.standardization.table_name_extractor import extract_table_name
        from backend.app.translation.hindi_translator import translate_dataframe, translate_text
        from backend.app.validation.table_validator import validate_table

        MSGS = [
            "cleaning rows",
            "reading headers",
            "merging header levels",
            "translating hindi",
            "validating tables",
            "naming tables",
        ]

        from backend.app.standardization.table_stitcher import stitch_tables
        import streamlit.components.v1 as _components

        import gc
        import shutil
        from backend.app.standardization.table_stitcher import _continues

        old_dir = st.session_state.get("workdir")
        if old_dir:
            shutil.rmtree(old_dir, ignore_errors=True)
        workdir = Path(tempfile.mkdtemp(prefix="datarubiks_"))
        csv_dir = workdir / "csv"
        csv_dir.mkdir()
        st.session_state["workdir"] = str(workdir)

        flushed, failed = [], []

        def _flush(item):
            """Write a finished table straight to disk and free its memory."""
            item["rows"], item["cols"] = item["df"].shape
            item["columns_list"] = [str(c) for c in item["df"].columns]
            item["df"].to_csv(csv_dir / f"table_{item['table_id']}.csv", index=False)
            item["df"] = None
            flushed.append(item)

        open_item = None
        t0 = time.time()
        prev_pct = 0.0
        step = max(1, len(tables) // 100)
        for i, t in enumerate(tables):
            if i % step == 0 or i == len(tables) - 1:
                pct = 100 * (i + 1) / len(tables)
                elapsed = time.time() - t0
                eta = _fmt_eta(elapsed / (i + 1) * (len(tables) - i - 1)) if i else ""
                with status_ph:
                    _components.html(
                        status_anim(
                            MSGS[i % len(MSGS)],
                            f"table {t['table_id']} · page {t['page']} · {i + 1} / {len(tables)}",
                            prev_pct, pct, eta,
                        ),
                        height=110,
                    )
                prev_pct = pct
            try:
                with redirect_stdout(io.StringIO()):
                    df = clean_dataframe(t["dataframe"])
                    t["dataframe"] = None
                    df = reassemble_wrapped_rows(df)
                    df = translate_dataframe(df)
                    h = detect_header_rows(df)
                    nm = extract_table_name(
                        df, h, translate_text(t.get("caption") or "") or None
                    )
                    df = apply_headers(df, h)
                    df = clean_headers(df)
                s = validate_table(df)
                if s["passed"]:
                    it = {"table_id": t["table_id"], "name": nm,
                          "page": t["page"], "df": df, "pages": [t["page"]]}
                    # incremental stitch: only the current table stays in memory
                    if open_item is not None and _continues(open_item, it):
                        open_item["df"] = pd.concat(
                            [open_item["df"], it["df"]], ignore_index=True
                        )
                        open_item["pages"].append(it["page"])
                        if not open_item["name"] and it["name"]:
                            open_item["name"] = it["name"]
                    else:
                        if open_item is not None:
                            _flush(open_item)
                        open_item = it
                else:
                    failed.append({"table": t["table_id"], "page": t["page"], "reason": s["reason"]})
            except Exception as e:
                failed.append({"table": t["table_id"], "page": t["page"], "reason": str(e)})

        if open_item is not None:
            _flush(open_item)

        if not flushed:
            st.warning("All tables failed validation.")
            st.stop()

        catalog = []
        unnamed_seq = 0
        for it in flushed:
            nm = it["name"]
            if not nm:
                unnamed_seq += 1
                nm = f"Table {unnamed_seq} (p.{it['page']})"
            if len(it["pages"]) > 1:
                nm += f" (pp. {it['pages'][0]}–{it['pages'][-1]})"
            catalog.append({
                "table_id": it["table_id"], "table_name": nm, "page": it["page"],
                "rows": it["rows"], "columns": it["cols"],
                "column_names": "|".join(it["columns_list"]),
            })

        with status_ph:
            _components.html(
                status_anim("building excel workbook + csv bundle",
                            f"{len(flushed)} tables", prev_pct, 100),
                height=110,
            )

        zip_path = workdir / "tables.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for it in flushed:
                p = csv_dir / f"table_{it['table_id']}.csv"
                zf.write(p, p.name)
            zf.writestr("table_catalog.csv", pd.DataFrame(catalog).to_csv(index=False))

        xlsx_path = workdir / "tables.xlsx"
        sources = {
            it["table_id"]: str(csv_dir / f"table_{it['table_id']}.csv")
            for it in flushed
        }
        _buf = build_workbook(sources, catalog)
        xlsx_path.write_bytes(_buf.getbuffer())
        del _buf

        n_raw_tables = len(tables)
        del tables
        gc.collect()

        st.session_state["results"] = {
            "catalog": catalog, "failed": failed,
            "csv_dir": str(csv_dir), "zip_path": str(zip_path),
            "xlsx_path": str(xlsx_path), "n_raw": n_raw_tables,
        }
        st.session_state["pdf_name"] = uploaded.name

    R = st.session_state["results"]
    catalog, failed = R["catalog"], R["failed"]

    # ── Results: solved cube right, stats + downloads left ────────────────────
    import streamlit.components.v1 as components

    with cube_ph:
        components.html(
            interactive_cube(
                "drag to rotate &mdash; solved",
                "&#9654; &nbsp;play with your hand",
                scramble=False,
            ),
            height=520,
        )

    fail_cls = "bad" if failed else "good"
    pages_covered = max((m["page"] for m in catalog), default=0)
    base = uploaded.name.replace(".pdf", "")

    status_ph.markdown(f"""
    <div class="bpage bpage-l pop" style="border-radius:10px;border:none;
         max-width:660px;padding:22px 28px 24px;margin-bottom:18px">
      <div class="bk-kicker">The Extraction</div>
      <div class="bk-title" style="font-size:36px">Solved.</div>
      <div class="bk-stats">
        <div class="bk-stat pop pop-2"><div class="v">{R["n_raw"]}</div><div class="k">Tables found</div></div>
        <div class="bk-stat pop pop-2"><div class="v good">{len(catalog)}</div><div class="k">Extracted</div></div>
        <div class="bk-stat pop pop-3"><div class="v {fail_cls}">{len(failed)}</div><div class="k">Set aside</div></div>
        <div class="bk-stat pop pop-3"><div class="v">{pages_covered}</div><div class="k">Pages covered</div></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    with left:
        b1, b2 = st.columns(2)
        with b1:
            st.download_button("↓ Download Excel", Path(R["xlsx_path"]).read_bytes(), file_name=f"{base}_tables.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
        with b2:
            st.download_button("↓ Download CSVs", Path(R["zip_path"]).read_bytes(), file_name=f"{base}_tables.zip",
                               mime="application/zip", use_container_width=True)

    search = st.text_input("s", placeholder="Search by name or table ID…", label_visibility="collapsed")

    tab1, tab2, tab3 = st.tabs(["Index", "Preview", f"Set aside ({len(failed)})"])

    with tab1:
        catalog_df = pd.DataFrame(catalog)
        if search:
            mask = (
                catalog_df["table_name"].str.contains(search, case=False, na=False)
                | catalog_df["table_id"].astype(str).str.contains(search)
            )
            catalog_df = catalog_df[mask]
        st.dataframe(
            catalog_df[["table_id", "table_name", "page", "rows", "columns"]],
            use_container_width=True, hide_index=True, height=330,
            column_config={
                "table_id": st.column_config.NumberColumn("#", width=60),
                "table_name": st.column_config.TextColumn("Name", width="large"),
                "page": st.column_config.NumberColumn("Page", width=70),
                "rows": st.column_config.NumberColumn("Rows", width=70),
                "columns": st.column_config.NumberColumn("Cols", width=70),
            },
        )

    with tab2:
        options = {
            f"#{m['table_id']}  ·  {m['table_name']}  ·  p.{m['page']}": m["table_id"]
            for m in catalog
        }
        pc1, pc2 = st.columns([5, 1])
        with pc1:
            sel = st.selectbox("t", list(options.keys()), label_visibility="collapsed")
        tid = options[sel]
        df_prev = pd.read_csv(Path(R["csv_dir"]) / f"table_{tid}.csv",
                              dtype=str, keep_default_na=False)
        with pc2:
            st.download_button(f"↓ CSV", df_prev.to_csv(index=False),
                               file_name=f"table_{tid}.csv", mime="text/csv",
                               use_container_width=True)
        st.dataframe(df_prev, use_container_width=True, hide_index=True, height=300)

    with tab3:
        if failed:
            st.dataframe(pd.DataFrame(failed), use_container_width=True, hide_index=True, height=300)
        else:
            st.markdown('<div class="scan-sub" style="padding:20px">No failures — every table passed validation.</div>',
                        unsafe_allow_html=True)

finally:
    if _gate_held:
        _gate.release()
    os.unlink(pdf_path)
