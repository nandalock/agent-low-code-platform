'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function WorkflowListRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace('/dashboard/workflow'); }, [router]);
  return null;
}
