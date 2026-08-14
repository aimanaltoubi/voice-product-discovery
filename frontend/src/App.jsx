import Home from '@/pages/Home';

// Single-page app: no auth layer, no router needed.
// The original Base44 auth scaffolding (login/register/OAuth) was removed to
// keep the repo fully self-contained (see README "Migration from Base44").
export default function App() {
  return <Home />;
}
