import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClerkProvider } from '@clerk/clerk-react'
import App from './App'
import { ClerkAuthBridge } from './components/auth/ClerkAuthBridge'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

// Clerk is optional: when no publishable key is configured the app renders
// without Clerk and the legacy session/API-key auth remains the only path.
// This keeps local dev and machine-only deployments working without Clerk.
const clerkPublishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as
  | string
  | undefined

const tree = (
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {clerkPublishableKey ? (
      <ClerkProvider
        publishableKey={clerkPublishableKey}
        afterSignOutUrl="/"
      >
        {/* Bridges Clerk's live session token into the API client */}
        <ClerkAuthBridge />
        {tree}
      </ClerkProvider>
    ) : (
      tree
    )}
  </React.StrictMode>,
)
