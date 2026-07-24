// Minimal inline icon set — deliberately simple line icons (no icon-font/library
// dependency) shared between the bottom tab bar (mobile) and top nav (desktop).
const common = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" };

export const HomeIcon = (p) => (
  <svg {...common} {...p}><path d="M3 11.5 12 4l9 7.5" /><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9" /></svg>
);
export const ChatIcon = (p) => (
  <svg {...common} {...p}><path d="M4 5h16v11H8l-4 4V5Z" /></svg>
);
export const CasesIcon = (p) => (
  <svg {...common} {...p}><path d="M4 7h5l2 2h9v10H4V7Z" /></svg>
);
export const NetworkIcon = (p) => (
  <svg {...common} {...p}><circle cx="6" cy="6" r="2.3" /><circle cx="18" cy="6" r="2.3" /><circle cx="12" cy="18" r="2.3" /><path d="M7.8 7.5 10.5 16M16.2 7.5 13.5 16M8.3 6h7.4" /></svg>
);
export const MapIcon = (p) => (
  <svg {...common} {...p}><path d="M12 21s7-6.1 7-11.5A7 7 0 0 0 5 9.5C5 14.9 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.3" /></svg>
);
export const AnalyticsIcon = (p) => (
  <svg {...common} {...p}><path d="M4 20V10M12 20V4M20 20v-7" /></svg>
);
export const AlertsIcon = (p) => (
  <svg {...common} {...p}><path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 14 6 10Z" /><path d="M10 19a2 2 0 0 0 4 0" /></svg>
);
export const ProfileIcon = (p) => (
  <svg {...common} {...p}><circle cx="12" cy="8" r="3.4" /><path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" /></svg>
);
export const InsightsIcon = (p) => (
  <svg {...common} {...p}><path d="M12 3a5 5 0 0 0-3 9v2h6v-2a5 5 0 0 0-3-9Z" /><path d="M10 18h4M11 21h2" /></svg>
);
export const ShieldIcon = (p) => (
  <svg {...common} {...p}><path d="M12 3 19 6v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3Z" /></svg>
);
export const MicIcon = (p) => (
  <svg {...common} {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
);
export const SpeakerIcon = (p) => (
  <svg {...common} {...p}><path d="M4 9v6h4l5 4V5L8 9H4Z" /><path d="M17 9a4.5 4.5 0 0 1 0 6" /></svg>
);
export const SunIcon = (p) => (
  <svg {...common} {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></svg>
);
export const MoonIcon = (p) => (
  <svg {...common} {...p}><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" /></svg>
);
export const HistoryIcon = (p) => (
  <svg {...common} {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3.2 2" /></svg>
);
export const PlusIcon = (p) => (
  <svg {...common} {...p}><path d="M12 5v14M5 12h14" /></svg>
);
export const TrashIcon = (p) => (
  <svg {...common} {...p}><path d="M4 7h16M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7M18 7l-.8 12.4A2 2 0 0 1 15.2 21H8.8a2 2 0 0 1-2-1.6L6 7" /></svg>
);
export const BuildingIcon = (p) => (
  <svg {...common} {...p}><path d="M5 21V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v16M5 21h14M13 21v-6h4a1 1 0 0 1 1 1v5M8 7h0M8 11h0M8 15h0M11 7h0M11 11h0M11 15h0" /></svg>
);
export const SirenIcon = (p) => (
  <svg {...common} {...p}><path d="M12 3a4 4 0 0 1 4 4v6H8V7a4 4 0 0 1 4-4Z" /><path d="M6 13h12v3a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-3ZM4 21l1-4M20 21l-1-4M12 3V1" /></svg>
);
export const PinIcon = (p) => (
  <svg {...common} {...p}><path d="M12 21s7-6.1 7-11.5A7 7 0 0 0 5 9.5C5 14.9 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.3" /></svg>
);
export const DownloadIcon = (p) => (
  <svg {...common} {...p}><path d="M12 4v11M7.5 11 12 15.5 16.5 11" /><path d="M4 17v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2" /></svg>
);
export const SocialIcon = (p) => (
  <svg {...common} {...p}><path d="M4 20v-9M10 20V9M16 20v-6M20 20V4" /><circle cx="4" cy="8.5" r="1.6" /><circle cx="10" cy="6" r="1.6" /><circle cx="16" cy="11.5" r="1.6" /><circle cx="20" cy="3" r="1.6" /></svg>
);
export const CopyIcon = (p) => (
  <svg {...common} {...p}><rect x="8" y="8" width="12" height="13" rx="1.5" /><path d="M16 8V5a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 5v10A1.5 1.5 0 0 0 5 16.5h3" /></svg>
);
export const ThumbsUpIcon = (p) => (
  <svg {...common} {...p}><path d="M7 11v9H4.5A1.5 1.5 0 0 1 3 18.5v-6A1.5 1.5 0 0 1 4.5 11H7Zm0 0 3.5-7a2 2 0 0 1 2 2.2L11.8 10H18a2 2 0 0 1 2 2.4l-1.2 6A2 2 0 0 1 16.8 20H7" /></svg>
);
export const ThumbsDownIcon = (p) => (
  <svg {...common} {...p}><path d="M17 13V4h2.5A1.5 1.5 0 0 1 21 5.5v6a1.5 1.5 0 0 1-1.5 1.5H17Zm0 0-3.5 7a2 2 0 0 1-2-2.2l.7-3.8H6a2 2 0 0 1-2-2.4l1.2-6A2 2 0 0 1 7.2 4H17" /></svg>
);
export const RegenerateIcon = (p) => (
  <svg {...common} {...p}><path d="M4 12a8 8 0 0 1 14.2-5M20 12a8 8 0 0 1-14.2 5" /><path d="M18.5 3.5V7H15M5.5 20.5V17H9" /></svg>
);
export const EditIcon = (p) => (
  <svg {...common} {...p}><path d="M4 20h4l10.5-10.5a2 2 0 0 0-4-4L4 16v4Z" /><path d="M13.5 6.5l4 4" /></svg>
);
export const ChevronDownIcon = (p) => (
  <svg {...common} {...p}><path d="M6 9l6 6 6-6" /></svg>
);
export const SearchIcon = (p) => (
  <svg {...common} {...p}><circle cx="10.5" cy="10.5" r="6.5" /><path d="M20 20l-4.8-4.8" /></svg>
);
export const StopIcon = (p) => (
  <svg {...common} {...p}><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
);
export const ThumbtackIcon = (p) => (
  <svg {...common} {...p}><path d="M14.5 3.5l6 6-3 3-1-1-3.5 3.5.5 4-1.5 1.5-3.5-5-5.5 5.5-1-1L7 15l-5-3.5 1.5-1.5 4 .5L11 7l-1-1 3-3Z" /></svg>
);
export const SendIcon = (p) => (
  <svg {...common} {...p}><path d="M4 12 20 4l-6 16-2.5-6.5L4 12Z" /></svg>
);
