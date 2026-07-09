// Coze-style design tokens
export const T = {
  bg:         '#F7F8FA',
  sidebar:    '#1C2333',
  sidebarHover:'#262E3D',
  surface:    '#FFFFFF',
  text:       '#1D2129',
  secondary:  '#86909C',
  tertiary:   '#C9CDD4',
  accent:     '#3370FF',
  accentBg:   '#E8F0FF',
  border:     '#E5E6EB',
  hover:      '#F2F3F5',
  success:    '#00B42A',
  danger:     '#F53F3F',
  warning:    '#FF7D00',
} as const;

export const S = { xs:4, sm:8, md:12, base:16, lg:20, xl:24, xxl:32, xxxl:48, huge:64 } as const;

export const inputField: React.CSSProperties = {
  width:'100%', padding:'8px 10px', background:T.surface, border:`1px solid ${T.border}`,
  borderRadius:6, fontSize:13, color:T.text, outline:'none', boxSizing:'border-box',
  fontFamily:'inherit', lineHeight:1.5, marginTop:S.xs,
};

export const labelField: React.CSSProperties = {
  fontSize:13, fontWeight:500, color:T.text, display:'flex', flexDirection:'column',
};

export const btnPrimary: React.CSSProperties = {
  padding:`${S.sm}px ${S.base}px`, borderRadius:6, fontSize:13, cursor:'pointer',
  background:T.accent, color:'#fff', border:'none', fontWeight:500,
};

export const btnGhost: React.CSSProperties = {
  padding:`${S.sm}px ${S.base}px`, borderRadius:6, fontSize:13, cursor:'pointer',
  background:T.surface, border:`1px solid ${T.border}`, color:T.text,
};
