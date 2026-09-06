'use client';

// useTrajectoryBatch — 高频 delta 的 rAF 批处理（性能核心）
//
// reasoning streaming 可达数百 delta/轮；不能每 token 一次 React 渲染。
// 数据流：SSE parse（同步、零 React）→ pending buffer
//        → requestAnimationFrame flush（≤60fps 一次 dispatch）
//        → useReducer 批量应用 → 渲染
//
// 三个兜底：
//   1. 后台标签页 rAF 停摆 → 250ms interval 兜底 flush（返回前台不滞后）
//   2. buffer 超 200 条强制 flush（防极端突发积压）
//   3. done/卸载 → forceFlush() 立即落盘（防尾部事件滞留 buffer）
//
// nodesRef 同步镜像（flush 时同步更新）：异步 SSE 循环里读「当前已应用 node 状态」
// 不依赖 React state 闭包（done 收口 / 存档用）。

import { useCallback, useEffect, useReducer, useRef } from 'react';
import {
  TrajNode, TrajWireEvent, applyTrajBatch,
} from '@/lib/trajectory';

type Action = { type: 'batch'; evs: TrajWireEvent[] } | { type: 'reset' };

function reducer(state: TrajNode[], action: Action): TrajNode[] {
  if (action.type === 'reset') return [];
  return applyTrajBatch(state, action.evs);
}

const MAX_PENDING = 200;   // 极端突发积压阈值（超过直接 flush）
const FALLBACK_MS = 250;   // rAF 停摆兜底间隔（后台标签页）

export function useTrajectoryBatch() {
  const [nodes, dispatch] = useReducer(reducer, []);
  const nodesRef = useRef<TrajNode[]>([]);
  const pendingRef = useRef<TrajWireEvent[]>([]);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (timerRef.current !== null) { clearInterval(timerRef.current); timerRef.current = null; }
    const evs = pendingRef.current;
    pendingRef.current = [];
    if (evs.length) {
      nodesRef.current = applyTrajBatch(nodesRef.current, evs); // 同步镜像先行
      dispatch({ type: 'batch', evs });
    }
  }, []);

  const enqueue = useCallback((ev: TrajWireEvent) => {
    pendingRef.current.push(ev);
    if (pendingRef.current.length >= MAX_PENDING) { flush(); return; }
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(() => flush());
    }
    if (timerRef.current === null) {
      // 后台标签 rAF 不跑：interval 兜底。只在 pending 非空时真正 flush，正常空转零成本。
      timerRef.current = window.setInterval(() => {
        if (pendingRef.current.length && rafRef.current === null) flush();
      }, FALLBACK_MS);
    }
  }, [flush]);

  const forceFlush = useCallback(() => flush(), [flush]);

  const reset = useCallback(() => {
    flush(); // 先清掉上个 turn 可能的尾部滞留事件
    nodesRef.current = [];
    dispatch({ type: 'reset' });
  }, [flush]);

  // 卸载清理：未 flush 的滞留事件丢弃（下个 turn reset 时也会清）
  useEffect(() => () => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    if (timerRef.current !== null) clearInterval(timerRef.current);
  }, []);

  return { nodes, nodesRef, enqueue, forceFlush, reset };
}
