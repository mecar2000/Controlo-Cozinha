import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { SandboxApp } from './SandboxApp'
import '../index.css'

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root')

createRoot(container).render(
  <StrictMode>
    <SandboxApp />
  </StrictMode>,
)
