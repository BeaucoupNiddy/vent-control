/* Line icons for the Home page: 24px grid, 1.8px stroke, currentColor. */

export function HIcon({ name, size = 20 }: { name: string; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "fan":
      return (<svg {...p}><circle cx="12" cy="12" r="1.8" /><path d="M12 10.2c-.3-3.6.9-6.9 3.6-6.9 2.6 0 2.4 4.6-1.8 6.9M13.8 12c3.6-.3 6.9.9 6.9 3.6 0 2.6-4.6 2.4-6.9-1.8M12 13.8c.3 3.6-.9 6.9-3.6 6.9-2.6 0-2.4-4.6 1.8-6.9M10.2 12c-3.6.3-6.9-.9-6.9-3.6 0-2.6 4.6-2.4 6.9 1.8" /></svg>);
    case "home": return (<svg {...p}><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-5h4v5" /></svg>);
    case "sun": return (<svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>);
    case "moon": return (<svg {...p}><path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z" /></svg>);
    case "drop": return (<svg {...p}><path d="M12 3s6 7 6 11a6 6 0 01-12 0c0-4 6-11 6-11z" /></svg>);
    case "bucket": return (<svg {...p}><path d="M4 7h16l-2 13H6L4 7z" /><path d="M4 7c0-2 3.6-3 8-3s8 1 8 3" /><path d="M9 12c1 1 5 1 6 0" /></svg>);
    case "clock": return (<svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>);
    case "bolt": return (<svg {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" /></svg>);
    case "spark": return (<svg {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" /><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7L19 16z" /></svg>);
    case "rocket": return (<svg {...p}><path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2" /><path d="M9 15l-3-3c1-4 5-9 13-9 0 8-5 12-9 13l-1-1z" /><circle cx="15" cy="9" r="1.6" /></svg>);
    case "power": return (<svg {...p}><path d="M12 3v9" /><path d="M6.3 6.3a8 8 0 1011.4 0" /></svg>);
    case "shield": return (<svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M8.5 12l2.5 2.5 4.5-5" /></svg>);
    case "shield-alert": return (<svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M12 8v5M12 16h.01" /></svg>);
    case "wind": return (<svg {...p}><path d="M3 8h11a3 3 0 10-3-3" /><path d="M3 12h16a3 3 0 11-3 3" /><path d="M3 16h7" /></svg>);
    case "up": return (<svg {...p}><path d="M12 19V5M6 11l6-6 6 6" /></svg>);
    case "down": return (<svg {...p}><path d="M12 5v14M6 13l6 6 6-6" /></svg>);
    case "flat": return (<svg {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);
    case "bell": return (<svg {...p}><path d="M6 16V11a6 6 0 0112 0v5l2 2H4l2-2z" /><path d="M10 21h4" /></svg>);
    case "thermo": return (<svg {...p}><path d="M10 14V5a2 2 0 014 0v9a4 4 0 11-4 0z" /><path d="M12 9v7" /></svg>);
    case "wall": return (<svg {...p}><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M3 9h18M3 15h18M9 4v5M15 9v6M9 15v5" /></svg>);
    case "arrow": return (<svg {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);
    case "check": return (<svg {...p}><path d="M5 12l5 5L20 7" /></svg>);
    case "x": return (<svg {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>);
    case "router": return (<svg {...p}><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 16.5h.01M11 16.5h.01" /><path d="M8.5 9.5a5 5 0 017 0M6 7a8.5 8.5 0 0112 0" /></svg>);
    case "plug": return (<svg {...p}><path d="M9 3v5M15 3v5" /><path d="M6 8h12v3a6 6 0 01-12 0V8z" /><path d="M12 17v4" /></svg>);
    case "cloud": return (<svg {...p}><path d="M7 18a4 4 0 01-.6-8A6 6 0 0118 9a4.5 4.5 0 01-.5 9H7z" /></svg>);
    case "sensor": return (<svg {...p}><rect x="7" y="3" width="10" height="18" rx="3" /><path d="M10 7h4M12 14a2 2 0 100 4 2 2 0 000-4z" /></svg>);
    case "probe": return (<svg {...p}><path d="M12 3v11" /><circle cx="12" cy="17" r="3" /><path d="M3 21h18" /><path d="M8 7h8" /></svg>);
    case "target": return (<svg {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></svg>);
    case "chart": return (<svg {...p}><path d="M4 20V4" /><path d="M4 20h16" /><path d="M7 15l4-4 3 3 5-6" /></svg>);
    case "grid": return (<svg {...p}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></svg>);
    case "sliders": return (<svg {...p}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>);
    case "chip": return (<svg {...p}><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" /></svg>);
    case "chevron": return (<svg {...p}><path d="M6 9l6 6 6-6" /></svg>);
    case "info": return (<svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>);
    case "alert": return (<svg {...p}><path d="M12 3l10 18H2L12 3z" /><path d="M12 10v5M12 18h.01" /></svg>);
    case "dehum": return (<svg {...p}><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8" /><path d="M12 11s2.5 2.8 2.5 4.4a2.5 2.5 0 01-5 0c0-1.6 2.5-4.4 2.5-4.4z" /></svg>);
    default: return null;
  }
}
