import React, { type ReactNode } from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const INK = "#102627";
const PAPER = "#f3f0e7";
const ORANGE = "#e66b37";
const MINT = "#8cd5b5";
const AMBER = "#ff8f00";
const MONO = 'Consolas, "Courier New", monospace';
const SANS = "Arial, sans-serif";
const SERIF = 'Georgia, "Times New Roman", serif';

export const LEGACY_DURATION_SECONDS = 12;
export const SIGNALS_DURATION_SECONDS = 10;
export const PEOPLE_DURATION_SECONDS = 8;
export const CLOSING_DURATION_SECONDS = 5;

const at = (seconds: number, fps: number) => seconds * fps;

function ramp(frame: number, from: number, to: number) {
  return interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
}

function value(frame: number, from: number, to: number, start: number, end: number) {
  return interpolate(frame, [from, to], [start, end], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });
}

type TextProps = {
  x: number;
  y: number;
  children: ReactNode;
  size?: number;
  fill?: string;
  family?: string;
  weight?: number | string;
  tracking?: number;
  anchor?: "start" | "middle" | "end";
  italic?: boolean;
  opacity?: number;
};

function T({
  x,
  y,
  children,
  size = 20,
  fill = INK,
  family = SANS,
  weight = 400,
  tracking = 0,
  anchor = "start",
  italic = false,
  opacity = 1,
}: TextProps) {
  return (
    <text
      x={x}
      y={y}
      fill={fill}
      fontFamily={family}
      fontSize={size}
      fontWeight={weight}
      fontStyle={italic ? "italic" : undefined}
      letterSpacing={tracking}
      textAnchor={anchor}
      opacity={opacity}
    >
      {children}
    </text>
  );
}

function Rule({ x1, y1, x2, y2, color = INK, opacity = 0.3, width = 1 }: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color?: string;
  opacity?: number;
  width?: number;
}) {
  return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={width} opacity={opacity} />;
}

/** The opening interlude: a dispatch board starts composed, then buckles under accumulating events. */
export function Legacy() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const boardIn = ramp(frame, at(0, fps), at(0.75, fps));
  const boardOut = 1 - ramp(frame, at(9.35, fps), at(10.15, fps));
  const darken = ramp(frame, at(9.35, fps), at(10.15, fps));
  const lineProgress = ramp(frame, at(9.65, fps), at(10.8, fps));
  const questionIn = ramp(frame, at(9.7, fps), at(10.25, fps));
  const pile = ramp(frame, at(6.1, fps), at(8.55, fps));
  const alertRows = [
    { time: "08:14", title: "URGENT CALL", detail: "NEW TRANSFER REQUEST", start: 1.8 },
    { time: "08:16", title: "CREW / R-02", detail: "BREAKDOWN REPORTED", start: 3.0 },
    { time: "08:18", title: "EAST ROUTE", detail: "ROAD CLOSURE", start: 4.2 },
    { time: "08:19", title: "CALL 014", detail: "PRIORITY RAISED", start: 5.35 },
  ];
  const pending = alertRows.filter((event) => frame >= at(event.start, fps)).length;
  const pilePositions = [
    { x: 152, y: 355 },
    { x: 805, y: 405 },
    { x: 350, y: 555 },
    { x: 1015, y: 655 },
  ];
  const assignments = [
    ["UNIT R-04", "PATIENT TRANSFER", "NODE B2  →  CLINIC 02", "EN ROUTE"],
    ["UNIT R-02", "EQUIPMENT MOVE", "DEPOT  →  NODE D4", "ASSIGNED"],
    ["UNIT R-07", "SCHEDULED CALL", "NODE F3  →  HUB 01", "READY"],
    ["UNIT R-01", "RETURN LEG", "CLINIC 01  →  STATION", "WAITING"],
    ["UNIT R-05", "PICKUP WINDOW", "NODE C5  →  CLINIC 02", "QUEUED"],
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: "#111918", overflow: "hidden" }}>
      <svg width="100%" height="100%" viewBox="0 0 1920 1080" role="img" aria-label="Illustrated dispatch board overwhelmed by incoming calls and disruptions">
        <defs>
          <linearGradient id="dispatch-glass" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#b2b9a9" />
            <stop offset="1" stopColor="#929b8d" />
          </linearGradient>
          <radialGradient id="dispatch-vignette">
            <stop offset="45%" stopColor="#17201d" stopOpacity="0" />
            <stop offset="100%" stopColor="#111918" stopOpacity="0.28" />
          </radialGradient>
          <pattern id="dispatch-scan" width="5" height="5" patternUnits="userSpaceOnUse">
            <rect width="5" height="1" fill="#17201d" opacity="0.18" />
          </pattern>
          <pattern id="dispatch-grid" width="34" height="34" patternUnits="userSpaceOnUse">
            <path d="M 34 0 L 0 0 0 34" fill="none" stroke="#31413a" strokeWidth="1" opacity="0.2" />
          </pattern>
        </defs>
        <rect width="1920" height="1080" fill="#111918" />
        <g opacity={boardIn * boardOut}>
          <rect x="78" y="55" width="1764" height="970" rx="22" fill="#252d29" stroke="#59625a" strokeWidth="3" />
          <rect x="93" y="70" width="1734" height="940" rx="12" fill="url(#dispatch-glass)" />
          <rect x="93" y="70" width="1734" height="940" rx="12" fill="url(#dispatch-grid)" />

          <rect x="93" y="70" width="1734" height="94" fill="#26302a" />
          <circle cx="125" cy="117" r="8" fill={MINT} />
          <T x={153} y={123} size={23} fill="#d6dbc9" family={MONO} tracking={2} weight={600}>DISPATCH / OPERATIONS BOARD</T>
          <T x={850} y={122} size={15} fill="#aeb8a8" family={MONO} tracking={2}>WORKFLOW ILLUSTRATION</T>
          <T x={1780} y={122} size={19} fill="#e4e8d9" family={MONO} anchor="end">DEMO CLOCK / 08:14</T>
          <Rule x1={93} y1={164} x2={1827} y2={164} color="#151e1b" opacity={0.7} width={2} />

          <rect x="128" y="198" width="987" height="754" fill="#aab2a2" stroke="#5d685e" strokeWidth="2" />
          <rect x="1140" y="198" width="646" height="754" fill="#a5ad9e" stroke="#5d685e" strokeWidth="2" />
          <T x={157} y={252} size={40} fill="#26332d" family={SERIF} weight={600}>One shift. Many decisions.</T>
          <T x={160} y={300} size={18} fill="#28342d" family={MONO} tracking={1.7} weight={600}>ACTIVE ASSIGNMENTS</T>
          <T x={1077} y={300} size={16} fill="#38433a" family={MONO} anchor="end">5 UNITS / 3 TASKS</T>
          <T x={1172} y={300} size={18} fill="#28342d" family={MONO} tracking={1.7} weight={600}>INCOMING / EVENT LOG</T>
          <T x={1760} y={300} size={15} fill="#38433a" family={MONO} anchor="end">{String(pending).padStart(2, "0")} PENDING</T>
          <Rule x1={152} y1={323} x2={1090} y2={323} color="#405047" opacity={0.55} />
          <Rule x1={1165} y1={323} x2={1760} y2={323} color="#405047" opacity={0.55} />

          <g opacity={0.62}>
            <T x={164} y={356} size={14} fill="#536055" family={MONO} tracking={1}>UNIT</T>
            <T x={344} y={356} size={14} fill="#536055" family={MONO} tracking={1}>ASSIGNMENT</T>
            <T x={647} y={356} size={14} fill="#536055" family={MONO} tracking={1}>ROUTE / DESTINATION</T>
            <T x={1066} y={356} size={14} fill="#536055" family={MONO} tracking={1} anchor="end">STATE</T>
          </g>
          {assignments.map((row, index) => {
            const reveal = index < 3 ? 1 : ramp(frame, at(index === 3 ? 5.2 : 6.9, fps), at(index === 3 ? 5.8 : 7.5, fps));
            const y = 402 + index * 105;
            const unstable = index > 1 ? ramp(frame, at(5.5, fps), at(8.8, fps)) : 0;
            return (
              <g key={row[0]} opacity={reveal * (1 - unstable * 0.24)}>
                <rect x="151" y={y - 34} width="941" height="86" fill={index % 2 === 0 ? "#c1c7b9" : "#b8c0b1"} opacity="0.72" />
                <rect x="151" y={y - 34} width="5" height="86" fill={index === 1 ? ORANGE : "#536457"} opacity={index === 1 ? 0.9 : 0.6} />
                <T x={174} y={y} size={19} fill="#26322d" family={MONO} weight={600}>{row[0]}</T>
                <T x={344} y={y} size={16} fill="#39463c" family={MONO}>{row[1]}</T>
                <T x={647} y={y} size={16} fill="#39463c" family={MONO}>{row[2]}</T>
                <T x={1066} y={y} size={14} fill={row[3] === "READY" ? "#3b6a51" : "#455148"} family={MONO} anchor="end" weight={600}>{row[3]}</T>
                <Rule x1={163} y1={y + 24} x2={1080} y2={y + 24} color="#667266" opacity={0.32} />
              </g>
            );
          })}

          <g opacity={ramp(frame, at(0.35, fps), at(0.75, fps))}>
            <rect x="155" y="908" width="925" height="30" fill="#324139" opacity="0.9" />
            <T x={174} y={929} size={13} fill="#d6ddce" family={MONO} tracking={1}>QUEUE LOAD</T>
            <rect x="350" y="918" width="390" height="8" rx="4" fill="#69776c" />
            <rect x="350" y="918" width={Math.max(8, 390 * (pending / 4))} height="8" rx="4" fill={pending >= 3 ? ORANGE : MINT} />
            <T x={1055} y={929} size={13} fill={pending >= 3 ? "#f0a27b" : "#d6ddce"} family={MONO} anchor="end">{pending === 0 ? "STABLE / 00 PENDING" : `${String(pending).padStart(2, "0")} PENDING / REPLAN`}</T>
          </g>

          {alertRows.map((event, index) => {
            const enter = ramp(frame, at(event.start, fps), at(event.start + 0.42, fps));
            const slotX = value(frame, at(event.start, fps), at(event.start + 0.65, fps), 1810, 1165);
            const slotY = 342 + index * 138;
            const pileX = value(frame, at(6.1, fps), at(8.55, fps), slotX, pilePositions[index].x);
            const pileY = value(frame, at(6.1, fps), at(8.55, fps), slotY, pilePositions[index].y);
            const x = slotX + (pileX - slotX) * pile;
            const y = slotY + (pileY - slotY) * pile;
            const tilt = (index % 2 === 0 ? -1 : 1) * pile * (index + 1) * 0.8;
            const flash = index === 0 || index === 1 || index === 2 ? 0.55 + 0.45 * Math.sin(frame * 0.2 + index) : 1;
            const width = 600 + 120 * pile;
            const height = 112 + 24 * pile;
            return (
              <g key={event.title} opacity={enter * boardOut} transform={`translate(${x} ${y}) rotate(${tilt} ${width / 2} ${height / 2})`}>
                <rect x="0" y="0" width={width} height={height} fill={index === 1 ? "#29322d" : "#d9ddd0"} stroke={index === 1 || index === 0 ? ORANGE : "#647064"} strokeWidth={index === 1 || index === 0 ? 4 : 3} />
                <rect x="0" y="0" width={12 + 4 * pile} height={height} fill={index === 1 || index === 2 || index === 0 ? ORANGE : "#566358"} opacity={flash} />
                <T x={34} y={33 + 4 * pile} size={16 + 2 * pile} fill={index === 1 ? "#cbd0c3" : "#5a665a"} family={MONO} tracking={1}>{event.time} / INCOMING</T>
                <T x={34} y={69 + 8 * pile} size={25 + 7 * pile} fill={index === 1 ? "#f1eee3" : "#25322c"} family={MONO} weight={700}>{event.title}</T>
                <T x={34} y={96 + 18 * pile} size={15 + 4 * pile} fill={index === 1 ? "#e6a17b" : "#576259"} family={MONO} tracking={1}>{event.detail}</T>
                <circle cx={width - 32} cy={29} r={8 + 2 * pile} fill={index === 1 || index === 2 || index === 0 ? ORANGE : MINT} opacity={flash} />
              </g>
            );
          })}

          <g opacity={ramp(frame, at(2.3, fps), at(3.0, fps)) * (1 - darken)}>
            <path d="M 1088 504 C 1120 504 1124 490 1160 490" fill="none" stroke={ORANGE} strokeWidth="3" strokeDasharray="7 7" />
            <circle cx="1088" cy="504" r="7" fill={ORANGE} />
          </g>
          <rect x="93" y="70" width="1734" height="940" rx="12" fill="url(#dispatch-scan)" opacity="0.5" />
          <rect x="93" y="70" width="1734" height="940" rx="12" fill="url(#dispatch-vignette)" />
        </g>
        <rect width="1920" height="1080" fill="#080d0c" opacity={darken} />
        <path
          d="M 106 646 C 430 645 598 648 820 646 C 1110 644 1362 647 1814 646"
          fill="none"
          stroke={ORANGE}
          strokeWidth="5"
          strokeLinecap="round"
          pathLength={1000}
          strokeDasharray={1000}
          strokeDashoffset={1000 * (1 - lineProgress)}
          opacity={ramp(frame, at(9.7, fps), at(10.05, fps))}
        />
        <g opacity={questionIn}>
          <T x={148} y={484} size={68} fill={PAPER} family={SERIF}>Which call</T>
          <T x={148} y={568} size={68} fill={ORANGE} family={SERIF} italic>comes first?</T>
        </g>
      </svg>
    </AbsoluteFill>
  );
}

function Car({ x, y, horizontal = false, ambulance = false, dimmed = false }: {
  x: number;
  y: number;
  horizontal?: boolean;
  ambulance?: boolean;
  dimmed?: boolean;
}) {
  const rotate = horizontal ? 90 : 0;
  const body = ambulance ? "#f8f5eb" : dimmed ? "#344646" : "#455a59";
  const glass = ambulance ? "#8aaca9" : "#243536";
  return (
    <g transform={`translate(${x} ${y}) rotate(${rotate})`}>
      <rect x="-23" y="-48" width="46" height="96" rx="13" fill={body} stroke={ambulance ? ORANGE : "#82918a"} strokeWidth={ambulance ? 3 : 1.5} />
      <rect x="-16" y="-27" width="32" height="23" rx="5" fill={glass} />
      <rect x="-16" y="12" width="32" height="20" rx="5" fill={glass} opacity="0.86" />
      <rect x="-18" y="-40" width="8" height="4" rx="2" fill="#e9e5d9" />
      <rect x="10" y="-40" width="8" height="4" rx="2" fill="#e9e5d9" />
      {ambulance ? (
        <>
          <rect x="-20" y="-1" width="40" height="9" fill={ORANGE} />
          <path d="M 0 -1 L 0 8 M -7 3.5 L 7 3.5" stroke="#fffaf1" strokeWidth="3" />
          <rect x="-11" y="-53" width="22" height="5" rx="2" fill={ORANGE} />
        </>
      ) : null}
    </g>
  );
}

function SignalHead({ x, y, active }: { x: number; y: number; active: "red" | "yellow" | "green" }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="-18" y="-49" width="36" height="98" rx="16" fill="#111d1e" stroke="#738681" strokeWidth="2" />
      <circle cx="0" cy="-27" r="9" fill={active === "red" ? "#f05d50" : "#572e2b"} />
      <circle cx="0" cy="0" r="9" fill={active === "yellow" ? ORANGE : "#68502b"} />
      <circle cx="0" cy="27" r="9" fill={active === "green" ? MINT : "#294337"} />
    </g>
  );
}

/** A signal-priority request shown inside the existing SUMO phase sequence. */
export function Signals() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const request = ramp(frame, at(1.25, fps), at(1.8, fps));
  const shorten = ramp(frame, at(2.45, fps), at(3.25, fps));
  const clearance = ramp(frame, at(4.35, fps), at(4.9, fps));
  const targetGreen = ramp(frame, at(5.25, fps), at(5.85, fps));
  const passage = ramp(frame, at(5.75, fps), at(8.2, fps));
  const normal = ramp(frame, at(8.25, fps), at(8.9, fps));
  const conflictLight: "red" | "yellow" | "green" = normal > 0.5
    ? "green"
    : targetGreen > 0.25 || clearance > 0.82
      ? "red"
      : clearance > 0.18
        ? "yellow"
        : "green";
  const approachLight: "red" | "yellow" | "green" = normal > 0.5 ? "red" : targetGreen > 0.25 ? "green" : clearance > 0.18 ? "red" : "red";
  const ambulanceY = value(frame, at(0, fps), at(5.55, fps), 935, 840) * (1 - passage) + value(frame, at(5.75, fps), at(8.1, fps), 840, 320) * passage;
  const zoom = value(frame, at(0, fps), at(5.2, fps), 0.96, 1.075) * (1 - passage * 0.4) + passage * 0.4;
  const steps = [
    { n: "01", title: "ELIGIBLE APPROACH", detail: "Urgent task · ETA 18 s", start: 1.1, end: 2.45 },
    { n: "02", title: "CONFLICTING GREEN", detail: "Shorten once · 6 s minimum", start: 2.35, end: 4.25 },
    { n: "03", title: "CLEARANCE", detail: "Full yellow; all-red if programmed", start: 4.2, end: 5.5 },
    { n: "04", title: "TARGET GREEN", detail: "Ambulance passes the junction", start: 5.45, end: 8.15 },
    { n: "05", title: "CYCLE CONTINUES", detail: "Normal program carries on", start: 8.1, end: 9.5 },
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: PAPER, overflow: "hidden" }}>
      <svg width="100%" height="100%" viewBox="0 0 1920 1080" role="img" aria-label="Diagrammatic SUMO signal sequence with an eligible ambulance approach and programmed clearances">
        <defs>
          <pattern id="signal-dots" width="22" height="22" patternUnits="userSpaceOnUse">
            <circle cx="1.5" cy="1.5" r="1" fill={INK} opacity="0.13" />
          </pattern>
          <linearGradient id="road-sheen" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#304646" />
            <stop offset="1" stopColor="#233838" />
          </linearGradient>
        </defs>
        <rect width="1920" height="1080" fill={PAPER} />
        <rect width="1920" height="1080" fill="url(#signal-dots)" opacity="0.38" />

        <T x={96} y={97} size={15} fill={ORANGE} family={MONO} tracking={2} weight={700}>D / SIGNAL PRIORITY</T>
        <T x={96} y={176} size={52} fill={INK} family={SERIF} weight={400}>Priority, inside the cycle.</T>
        <T x={96} y={216} size={18} fill="#526361" family={MONO} tracking={1}>ELIGIBLE SIMULATED APPROACH  /  PROGRAMMED CLEARANCES PRESERVED</T>
        <rect x="1530" y="83" width="294" height="48" rx="24" fill={INK} />
        <circle cx="1557" cy="107" r="6" fill={MINT} />
        <T x={1577} y={113} size={15} fill={PAPER} family={MONO} tracking={1}>SUMO · SIMULATION ONLY</T>

        <rect x="94" y="256" width="1243" height="718" fill="#e9e6dc" stroke="#c5c7bb" strokeWidth="2" />
        <g transform={`translate(858 613) scale(${zoom}) translate(-858 -613)`}>
          <rect x="94" y="382" width="1243" height="468" fill="#dce0d5" />
          <path d="M 94 382 H 1337 M 94 850 H 1337 M 250 256 V 974 M 1320 256 V 974" stroke="#c1c9be" strokeWidth="2" opacity="0.6" />
          <path d="M 130 302 L 314 302 M 1110 905 L 1308 905 M 130 905 L 242 905 M 1118 302 L 1308 302" stroke="#c0c8bd" strokeWidth="3" />
          <rect x="664" y="256" width="382" height="718" fill="url(#road-sheen)" />
          <rect x="94" y="450" width="1243" height="332" fill="url(#road-sheen)" />
          <rect x="664" y="450" width="382" height="332" fill="#2a4141" />
          <path d="M 854 256 V 449 M 854 783 V 974" stroke="#82918a" strokeWidth="2" strokeDasharray="20 19" opacity="0.72" />
          <path d="M 664 616 H 663 M 1047 616 H 1337" stroke="#82918a" strokeWidth="2" strokeDasharray="20 19" opacity="0.72" />
          <path d="M 756 256 V 450 M 956 256 V 450 M 756 782 V 974 M 956 782 V 974" stroke="#8c9a92" strokeWidth="2" strokeDasharray="17 16" opacity="0.48" />
          <path d="M 94 531 H 663 M 1047 531 H 1337 M 94 699 H 663 M 1047 699 H 1337" stroke="#8c9a92" strokeWidth="2" strokeDasharray="17 16" opacity="0.48" />
          <path d="M 664 438 H 1046 M 664 793 H 1046" stroke="#f3f0e7" strokeWidth="4" opacity="0.86" />
          <path d="M 646 461 v30 m0 11v30 m0 11v30 m0 11v30 m0 11v30 m0 11v30 M 1064 461 v30 m0 11v30 m0 11v30 m0 11v30 m0 11v30 m0 11v30" stroke="#dedfd4" strokeWidth="9" opacity="0.88" />
          <path d="M 680 792 H 710 M 722 792 H 752 M 764 792 H 794 M 806 792 H 836 M 874 792 H 904 M 916 792 H 946 M 958 792 H 988 M 1000 792 H 1030" stroke={ORANGE} strokeWidth="4" opacity={request * 0.8} />

          <path d="M 914 852 l -10 -18 h 20 z M 914 808 l -10 -18 h 20 z M 788 423 l -10 18 h 20 z M 788 379 l -10 18 h 20 z" fill="#d7ded2" opacity="0.7" />
          <path d="M 458 568 l 20 -10 v 20 z M 502 568 l 20 -10 v 20 z M 1145 658 l -20 -10 v 20 z M 1101 658 l -20 -10 v 20 z" fill="#d7ded2" opacity="0.66" />

          <Car x={781} y={342} dimmed />
          <Car x={946} y={792} dimmed />
          <Car x={529} y={555} horizontal dimmed />
          <Car x={value(frame, at(0, fps), at(4.2, fps), 746, 1127)} y={548} horizontal dimmed />
          <Car x={1104} y={668} horizontal dimmed />
          <Car x={917} y={ambulanceY} ambulance />

          <SignalHead x={680} y={438} active={conflictLight} />
          <SignalHead x={1030} y={438} active={conflictLight} />
          <SignalHead x={680} y={792} active={approachLight} />
          <SignalHead x={1030} y={792} active={approachLight} />

          <g opacity={request}>
            <path d="M 916 907 C 922 850 922 822 918 760" fill="none" stroke={ORANGE} strokeWidth="3" strokeDasharray="8 10" />
            <circle cx="918" cy="760" r="15" fill="none" stroke={ORANGE} strokeWidth="3" />
          </g>
          <g opacity={targetGreen}>
            <path d="M 917 775 L 917 495" fill="none" stroke={MINT} strokeWidth="4" strokeDasharray="12 12" opacity="0.8" />
          </g>
        </g>

        <T x={130} y={298} size={13} fill="#62706c" family={MONO} tracking={1}>NORTH / SIMULATED ROAD GRID</T>
        <T x={1300} y={946} size={13} fill="#62706c" family={MONO} anchor="end">DIAGRAMMATIC VIEW</T>
        <g opacity={request}>
          <rect x="117" y="871" width="330" height="54" rx="27" fill={PAPER} stroke="#c7c9bd" strokeWidth="1.5" />
          <circle cx="143" cy="898" r="7" fill={ORANGE} />
          <T x={163} y={905} size={15} fill={INK} family={MONO} tracking={0.5}>ELIGIBLE · ETA 18 s</T>
        </g>

        <Rule x1={1383} y1={257} x2={1383} y2={974} color={INK} opacity={0.18} width={2} />
        <T x={1424} y={293} size={14} fill={ORANGE} family={MONO} tracking={2} weight={700}>ONE SAFE CYCLE</T>
        {steps.map((step, index) => {
          const begin = ramp(frame, at(step.start, fps), at(step.start + 0.32, fps));
          const done = ramp(frame, at(step.end, fps), at(step.end + 0.35, fps));
          const active = Math.max(0, begin - done * 0.45);
          const y = 356 + index * 112;
          const fill = index === 1 ? ORANGE : MINT;
          return (
            <g key={step.n} opacity={0.48 + active * 0.52}>
              <circle cx="1440" cy={y} r="16" fill={active > 0.15 ? fill : "#d6d6ca"} />
              <T x={1440} y={y + 5} size={12} fill={active > 0.15 ? INK : "#64716d"} family={MONO} anchor="middle" weight={700}>{step.n}</T>
              <T x={1470} y={y - 3} size={15} fill={INK} family={MONO} tracking={0.5} weight={700}>{step.title}</T>
              <T x={1470} y={y + 25} size={14} fill="#5e6c67" family={MONO}>{step.detail}</T>
              {index < steps.length - 1 ? <Rule x1={1440} y1={y + 20} x2={1440} y2={y + 89} color={INK} opacity={0.22} width={2} /> : null}
            </g>
          );
        })}

        <g opacity={shorten}>
          <rect x="1423" y="859" width="376" height="74" fill="#e9e6dc" stroke="#bfc3b8" />
          <T x={1442} y={886} size={13} fill="#62706c" family={MONO} tracking={0.8}>EXAMPLE REMAINING GREEN</T>
          <T x={1442} y={916} size={17} fill={INK} family={MONO} weight={700}>18 s  →  6 s FLOOR</T>
          <rect x="1668" y="894" width="104" height="10" rx="5" fill="#c9cdc2" />
          <rect x="1668" y="894" width={104 * (1 - shorten * 0.66)} height="10" rx="5" fill={ORANGE} />
        </g>

        <Rule x1={96} y1={1001} x2={1824} y2={1001} color={INK} opacity={0.22} />
        <T x={96} y={1034} size={14} fill="#596963" family={MONO} tracking={0.7}>YELLOW RUNS FULL · ALL-RED RUNS FULL WHERE PROGRAMMED</T>
        <T x={1824} y={1034} size={14} fill="#596963" family={MONO} anchor="end">NO MEASURED BENEFIT IMPLIED</T>
      </svg>
    </AbsoluteFill>
  );
}

function NoteIcon({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <path d="M 0 0 H 74 L 102 28 V 118 H 0 Z" fill="#e8e4d9" stroke="#9da8a0" strokeWidth="2" />
      <path d="M 74 0 V 28 H 102" fill="none" stroke="#9da8a0" strokeWidth="2" />
      <circle cx="25" cy="48" r="5" fill={ORANGE} />
      <Rule x1={38} y1={48} x2={79} y2={48} color={INK} opacity={0.46} width={2} />
      <Rule x1={20} y1={69} x2={80} y2={69} color={INK} opacity={0.28} width={2} />
      <Rule x1={20} y1={84} x2={72} y2={84} color={INK} opacity={0.28} width={2} />
    </g>
  );
}

function NavigationGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="0" y="0" width="98" height="155" rx="16" fill={INK} />
      <rect x="7" y="9" width="84" height="137" rx="10" fill="#e5e4d9" />
      <path d="M 12 36 L 34 24 L 51 44 L 76 30 L 91 42 M 11 105 L 33 88 L 53 106 L 83 82" fill="none" stroke="#9bac9d" strokeWidth="3" />
      <path d="M 24 111 C 35 90 40 70 57 67 C 70 65 69 51 75 46" fill="none" stroke={ORANGE} strokeWidth="5" strokeLinecap="round" strokeDasharray="2 1" />
      <circle cx="24" cy="111" r="6" fill={INK} stroke={PAPER} strokeWidth="2" />
      <path d="M 75 37 C 68 48 65 55 75 65 C 85 55 82 48 75 37 Z" fill={ORANGE} />
      <rect x="38" y="137" width="22" height="3" rx="2" fill="#727d77" />
    </g>
  );
}

/** A short editorial narrative of the practitioner-described report/decision/navigation handoff. */
export function People() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const columnOne = ramp(frame, at(0.25, fps), at(1.15, fps));
  const columnTwo = ramp(frame, at(1.75, fps), at(2.55, fps));
  const columnThree = ramp(frame, at(3.55, fps), at(4.45, fps));
  const lineProgress = ramp(frame, at(0.65, fps), at(5.45, fps));
  const closingNote = ramp(frame, at(5.3, fps), at(6.2, fps));

  return (
    <AbsoluteFill style={{ backgroundColor: PAPER, overflow: "hidden" }}>
      <svg width="100%" height="100%" viewBox="0 0 1920 1080" role="img" aria-label="Illustrated workflow from field report to dispatcher decision to the crew's existing navigation">
        <defs>
          <pattern id="paper-grain" width="96" height="96" patternUnits="userSpaceOnUse">
            <circle cx="10" cy="17" r="1" fill="#53625d" opacity="0.15" />
            <circle cx="61" cy="50" r="1" fill="#53625d" opacity="0.12" />
            <circle cx="82" cy="14" r="0.7" fill="#53625d" opacity="0.15" />
          </pattern>
        </defs>
        <rect width="1920" height="1080" fill={PAPER} />
        <rect width="1920" height="1080" fill="url(#paper-grain)" opacity="0.48" />
        <rect x="90" y="72" width="10" height="56" fill={ORANGE} />
        <T x={125} y={94} size={15} fill="#596963" family={MONO} tracking={2} weight={600}>E / FIELD WORKFLOW</T>
        <T x={125} y={162} size={54} fill={INK} family={SERIF}>Keep the handoff human.</T>
        <T x={125} y={204} size={18} fill="#5d6b65" family={MONO} tracking={0.8}>ONE QUALITATIVE PRACTITIONER CONVERSATION, DRAWN AS A WORKFLOW</T>

        <Rule x1={132} y1={340} x2={1778} y2={340} color="#aab2a7" opacity={0.76} width={2} />
        <path
          d="M 132 340 H 1778"
          fill="none"
          stroke={ORANGE}
          strokeWidth="3"
          pathLength={1000}
          strokeDasharray={1000}
          strokeDashoffset={1000 * (1 - lineProgress)}
        />
        {[460, 984, 1505].map((x, index) => {
          const opacity = index === 0 ? columnOne : index === 1 ? columnTwo : columnThree;
          return <circle key={x} cx={x} cy="340" r="10" fill={opacity > 0.2 ? ORANGE : "#aab2a7"} />;
        })}

        <g opacity={columnOne} transform={`translate(0 ${value(frame, at(0.25, fps), at(1.15, fps), 16, 0)})`}>
          <T x={132} y={294} size={14} fill={ORANGE} family={MONO} tracking={1.7} weight={700}>01 / REPORT</T>
          <NoteIcon x={132} y={405} />
          <T x={132} y={582} size={32} fill={INK} family={SERIF}>A call or crew report</T>
          <T x={132} y={625} size={32} fill={INK} family={SERIF}>reaches dispatch.</T>
          <T x={132} y={688} size={17} fill="#5d6b65" family={MONO}>New task · delay · disruption</T>
        </g>

        <g opacity={columnTwo} transform={`translate(0 ${value(frame, at(1.75, fps), at(2.55, fps), 16, 0)})`}>
          <T x={656} y={294} size={14} fill={ORANGE} family={MONO} tracking={1.7} weight={700}>02 / DISPATCHER</T>
          <rect x="656" y="408" width="250" height="146" fill="#e7e3d8" stroke="#bdc1b6" strokeWidth="2" />
          <rect x="656" y="408" width="250" height="34" fill={INK} />
          <T x={674} y={431} size={13} fill={PAPER} family={MONO} tracking={1}>OPTIONS / REVIEW</T>
          <T x={678} y={478} size={15} fill="#5c6c66" family={MONO}>01</T>
          <Rule x1={712} y1={473} x2={866} y2={473} color="#65736c" opacity={0.44} width={3} />
          <T x={678} y={517} size={15} fill={ORANGE} family={MONO} weight={700}>02</T>
          <Rule x1={712} y1={512} x2={842} y2={512} color={ORANGE} opacity={0.86} width={4} />
          <circle cx="884" cy="512" r="8" fill={ORANGE} />
          <T x={656} y={615} size={32} fill={INK} family={SERIF}>A person reviews</T>
          <T x={656} y={658} size={32} fill={INK} family={SERIF}>and decides.</T>
          <T x={656} y={718} size={17} fill="#5d6b65" family={MONO}>RescueFlow proposes; dispatcher approves.</T>
        </g>

        <g opacity={columnThree} transform={`translate(0 ${value(frame, at(3.55, fps), at(4.45, fps), 16, 0)})`}>
          <T x={1180} y={294} size={14} fill={ORANGE} family={MONO} tracking={1.7} weight={700}>03 / CREW ROUTING</T>
          <NavigationGlyph x={1184} y={400} />
          <T x={1320} y={447} size={29} fill={INK} family={SERIF}>The crew keeps</T>
          <T x={1320} y={486} size={29} fill={INK} family={SERIF}>its existing</T>
          <T x={1320} y={525} size={29} fill={INK} family={SERIF}>navigation.</T>
          <T x={1320} y={577} size={16} fill="#5d6b65" family={MONO}>Destination handed off</T>
          <T x={1320} y={602} size={16} fill="#5d6b65" family={MONO}>to the crew's app.</T>
          <Rule x1={1184} y1={630} x2={1786} y2={630} color="#aab2a7" opacity={0.66} />
          <T x={1184} y={671} size={15} fill="#65716c" family={MONO}>Google Maps was named in one conversation.</T>
        </g>

        <g opacity={closingNote}>
          <Rule x1={125} y1={812} x2={1790} y2={812} color={INK} opacity={0.18} />
          <T x={125} y={855} size={16} fill="#637069" family={MONO}>WORKFLOW ILLUSTRATION · ONE QUALITATIVE ACCOUNT</T>
          <T x={1790} y={855} size={16} fill="#637069" family={MONO} anchor="end">NOT REPRESENTATIVE EVIDENCE</T>
        </g>
      </svg>
    </AbsoluteFill>
  );
}

function BrandMark({ x, y, size }: { x: number; y: number; size: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${size / 64})`}>
      <rect width="64" height="64" fill="#000000" />
      <path d="M 32 8 56 32 32 56 8 32 Z" fill={AMBER} />
      <path d="M 32 22 42 32 32 42 22 32 Z" fill="#000000" />
    </g>
  );
}

/** Quiet branded end plate echoing the existing RescueFlow icon. */
export function Closing() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const markIn = ramp(frame, at(0.3, fps), at(1.05, fps));
  const titleIn = ramp(frame, at(0.8, fps), at(1.65, fps));
  const metaIn = ramp(frame, at(1.55, fps), at(2.15, fps));
  const lift = value(frame, at(0.8, fps), at(1.65, fps), 18, 0);

  return (
    <AbsoluteFill style={{ backgroundColor: INK, overflow: "hidden" }}>
      <svg width="100%" height="100%" viewBox="0 0 1920 1080" role="img" aria-label="RescueFlow AI closing slate for AdaHack 2026">
        <defs>
          <radialGradient id="closing-glow" cx="79%" cy="45%" r="68%">
            <stop offset="0" stopColor="#274041" stopOpacity="0.76" />
            <stop offset="100%" stopColor={INK} stopOpacity="0" />
          </radialGradient>
          <pattern id="closing-grid" width="80" height="80" patternUnits="userSpaceOnUse">
            <path d="M 80 0 L 0 0 0 80" fill="none" stroke="#f3f0e7" strokeWidth="1" opacity="0.055" />
          </pattern>
        </defs>
        <rect width="1920" height="1080" fill={INK} />
        <rect width="1920" height="1080" fill="url(#closing-glow)" />
        <rect width="1920" height="1080" fill="url(#closing-grid)" />
        <Rule x1={118} y1={134} x2={1802} y2={134} color={PAPER} opacity={0.15} width={1} />
        <T x={122} y={111} size={15} fill="#bdc8be" family={MONO} tracking={2}>ADA HACK / 2026</T>
        <T x={1798} y={111} size={15} fill="#bdc8be" family={MONO} anchor="end" tracking={2}>RESCUEFLOW AI</T>

        <g opacity={titleIn} transform={`translate(0 ${lift})`}>
          <T x={128} y={484} size={88} fill={PAPER} family={SERIF}>The next move</T>
          <T x={128} y={594} size={88} fill={ORANGE} family={SERIF} italic>is yours.</T>
          <Rule x1={132} y1={660} x2={795} y2={660} color={ORANGE} opacity={0.82} width={3} />
          <T x={132} y={710} size={17} fill="#bdc8be" family={MONO} tracking={1.6}>A DISPATCHER REMAINS IN CONTROL.</T>
        </g>

        <g opacity={markIn}>
          <rect x="1358" y="362" width="302" height="302" fill="#0a1415" stroke="#647873" strokeWidth="1.5" />
          <BrandMark x={1427} y={431} size={164} />
          <T x={1509} y={716} size={19} fill={PAPER} family={MONO} anchor="middle" tracking={1.8} weight={700}>RESCUEFLOW AI</T>
          <T x={1509} y={750} size={14} fill="#9aa9a1" family={MONO} anchor="middle" tracking={1.6}>ADA HACK 2026</T>
        </g>

        <g opacity={metaIn}>
          <Rule x1={118} y1={932} x2={1802} y2={932} color={PAPER} opacity={0.15} width={1} />
          <T x={122} y={971} size={14} fill="#9aa9a1" family={MONO} tracking={1.5}>A SIMULATION · A DECISION SUPPORT PROTOTYPE</T>
          <T x={1798} y={971} size={14} fill="#9aa9a1" family={MONO} anchor="end" tracking={1.5}>THE NEXT MOVE IS YOURS</T>
        </g>
      </svg>
    </AbsoluteFill>
  );
}
