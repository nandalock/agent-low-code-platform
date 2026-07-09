'use client';

import { useState, useEffect } from 'react';
import { T, S, inputField, labelField, btnPrimary } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const DEFAULT_CONFIG = { handoff_message: '', queue_message: '' };

export default function HumanHandoffPage() {
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  useEffect(() => {
    fetch(`${API}/api/agents/human_handoff/config`).then(r => r.json()).then(d => {
      if (d.config) {
        setConfig({
          handoff_message: d.config.handoff_message ?? '',
          queue_message: d.config.queue_message ?? '',
        });
      }
    }).catch(() => {});
  }, []);

  async function handleSaveConfig() {
    setSaving(true); setSaveMsg('');
    try {
      const r = await fetch(`${API}/api/agents/human_handoff/config`, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config) });
      setSaveMsg(r.ok?'保存成功':'保存失败');
    } catch { setSaveMsg('保存失败'); }
    finally { setSaving(false); }
  }

  return (
    <div style={{ display:'flex', height:'100vh', background:T.bg, color:T.text, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif" }}>
      <div style={{ flex:1, display:'flex', flexDirection:'column' }}>
        <div style={{ padding:`${S.lg}px ${S.xl}px ${S.base}px` }}>
          <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>人工转接</h3>
          <p style={{ margin:0, marginTop:S.xs, fontSize:13, color:T.secondary }}>配置转接人工客服的提示文案</p>
        </div>

        <div style={{ flex:1, padding:`0 ${S.xl}px`, maxWidth:600 }}>
          <div style={{ background:T.surface, borderRadius:8, padding:S.xl, border:`1px solid ${T.border}`, display:'flex', flexDirection:'column', gap:S.base }}>
            <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase' }}>转接文案</div>

            <label style={labelField}>转接提示语
              <p style={{ fontSize:12, color:T.secondary, margin:0, marginTop:2 }}>用户被转接到人工时显示的提示消息</p>
              <textarea rows={3} value={config.handoff_message} onChange={e => setConfig(p=>({...p, handoff_message:e.target.value}))}
                placeholder="已为您转接人工客服，请稍候..."
                style={{ ...inputField, resize:'vertical', minHeight:60 }} />
            </label>

            <label style={labelField}>排队提示语
              <p style={{ fontSize:12, color:T.secondary, margin:0, marginTop:2 }}>当前排队人数较多时显示，使用 {'{wait_minutes}'} 作为等待时间占位符</p>
              <textarea rows={3} value={config.queue_message} onChange={e => setConfig(p=>({...p, queue_message:e.target.value}))}
                placeholder="当前排队人数较多，预计等待 {wait_minutes} 分钟，人工客服将尽快为您服务。"
                style={{ ...inputField, resize:'vertical', minHeight:60 }} />
            </label>
          </div>

          <div style={{ marginTop:S.lg }}>
            {saveMsg && <div style={{ fontSize:12, marginBottom:S.sm, color:saveMsg==='保存成功'?T.success:T.danger }}>{saveMsg}</div>}
            <button onClick={handleSaveConfig} disabled={saving} style={{...btnPrimary, padding:'10px 32px', fontSize:14, opacity:saving?0.5:1 }}>{saving?'保存中...':'保存配置'}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
