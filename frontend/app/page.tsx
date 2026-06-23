import { redirect } from 'next/navigation';
import { cookies } from 'next/headers';

export default function Home() {
  const token = cookies().get('auth_token')?.value;
  redirect(token ? '/dashboard' : '/login');
}
