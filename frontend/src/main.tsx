import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { RecordingHud } from '@/components/RecordingHud'

const params = new URLSearchParams(window.location.search)
const isHud = params.get('view') === 'hud'
const sessionId = Number(params.get('session') ?? 0)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isHud ? <RecordingHud sessionId={sessionId} /> : <App />}
  </StrictMode>,
)
