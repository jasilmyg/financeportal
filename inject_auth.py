auth_html = """
  <!-- CYBERSECURITY AUTH WALL -->
  <div id="auth-wall">
    <style>
      #auth-wall {
        position: fixed;
        inset: 0;
        z-index: 10000;
        background: #020813;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        transition: opacity 0.8s ease, transform 0.8s ease;
        overflow: hidden;
      }
      #auth-wall.fade-out {
        opacity: 0;
        transform: scale(1.05);
        pointer-events: none;
      }
      
      /* Circuit Background Pattern */
      .auth-bg {
        position: absolute;
        inset: 0;
        opacity: 0.15;
        background-image: 
          linear-gradient(rgba(0, 229, 255, 0.2) 1px, transparent 1px),
          linear-gradient(90deg, rgba(0, 229, 255, 0.2) 1px, transparent 1px);
        background-size: 40px 40px;
        z-index: 1;
      }
      
      /* Glowing Tech Rings */
      .auth-rings-container {
        position: relative;
        width: 320px;
        height: 320px;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2;
        margin-bottom: 40px;
      }
      
      .auth-ring {
        position: absolute;
        border-radius: 50%;
        border: 2px solid transparent;
      }
      
      .ring-1 {
        width: 100%;
        height: 100%;
        border-top: 2px solid #00e5ff;
        border-right: 2px solid #00e5ff;
        box-shadow: 0 0 15px rgba(0, 229, 255, 0.4), inset 0 0 15px rgba(0, 229, 255, 0.4);
        animation: spin-right 8s linear infinite;
      }
      
      .ring-2 {
        width: 80%;
        height: 80%;
        border-left: 2px solid #00a0cc;
        border-bottom: 2px solid #00a0cc;
        animation: spin-left 6s linear infinite;
      }
      
      .ring-3 {
        width: 65%;
        height: 65%;
        border: 1px dashed #00e5ff;
        opacity: 0.6;
        animation: spin-right 12s linear infinite;
      }
      
      .ring-4 {
        width: 50%;
        height: 50%;
        border: 2px solid rgba(0, 229, 255, 0.2);
        box-shadow: 0 0 20px rgba(0, 229, 255, 0.5);
      }
      
      .auth-lock-icon {
        position: absolute;
        width: 64px;
        height: 64px;
        color: #00e5ff;
        filter: drop-shadow(0 0 10px #00e5ff);
        z-index: 3;
      }
      
      @keyframes spin-right { 100% { transform: rotate(360deg); } }
      @keyframes spin-left { 100% { transform: rotate(-360deg); } }
      
      /* Auth Form */
      .auth-form {
        position: relative;
        z-index: 2;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 20px;
        background: rgba(2, 13, 30, 0.7);
        padding: 30px 40px;
        border-radius: 12px;
        border: 1px solid rgba(0, 229, 255, 0.3);
        box-shadow: 0 0 30px rgba(0, 229, 255, 0.1);
        backdrop-filter: blur(10px);
      }
      
      .auth-title {
        color: #00e5ff;
        font-size: 18px;
        letter-spacing: 4px;
        text-transform: uppercase;
        margin: 0;
        text-shadow: 0 0 10px rgba(0, 229, 255, 0.5);
      }
      
      .auth-input-group {
        position: relative;
        width: 260px;
      }
      
      .auth-input {
        width: 100%;
        background: rgba(0, 30, 60, 0.5);
        border: 1px solid #00a0cc;
        color: #fff;
        padding: 12px 16px;
        border-radius: 6px;
        font-family: monospace;
        font-size: 16px;
        outline: none;
        transition: all 0.3s ease;
        text-align: center;
        letter-spacing: 4px;
      }
      
      .auth-input:focus {
        border-color: #00e5ff;
        box-shadow: 0 0 15px rgba(0, 229, 255, 0.4);
      }
      
      .auth-input::placeholder {
        color: rgba(0, 229, 255, 0.3);
        letter-spacing: 2px;
      }
      
      .auth-btn {
        width: 100%;
        background: linear-gradient(90deg, #0055aa, #00a0cc);
        color: #fff;
        border: none;
        padding: 12px;
        border-radius: 6px;
        font-size: 15px;
        font-weight: 600;
        letter-spacing: 1px;
        cursor: pointer;
        transition: all 0.3s ease;
        text-transform: uppercase;
        box-shadow: 0 0 15px rgba(0, 160, 204, 0.5);
      }
      
      .auth-btn:hover {
        background: linear-gradient(90deg, #0077dd, #00e5ff);
        box-shadow: 0 0 25px rgba(0, 229, 255, 0.8);
      }
      
      .auth-error {
        color: #ff3366;
        font-size: 13px;
        height: 15px;
        margin-top: -10px;
        opacity: 0;
        transition: opacity 0.3s ease;
      }
    </style>
    
    <div class="auth-bg"></div>
    
    <div class="auth-rings-container">
      <div class="auth-ring ring-1"></div>
      <div class="auth-ring ring-2"></div>
      <div class="auth-ring ring-3"></div>
      <div class="auth-ring ring-4"></div>
      <svg class="auth-lock-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
        <circle cx="12" cy="16" r="1"></circle>
      </svg>
    </div>
    
    <div class="auth-form">
      <h2 class="auth-title">System Locked</h2>
      <div class="auth-input-group">
        <input type="password" id="auth-password" class="auth-input" placeholder="ACCESS CODE" autocomplete="off">
      </div>
      <div class="auth-error" id="auth-error">ACCESS DENIED</div>
      <button class="auth-btn" onclick="checkAuth()">Authenticate</button>
    </div>
    
    <script>
      function checkAuth() {
        const pwd = document.getElementById('auth-password').value;
        const err = document.getElementById('auth-error');
        // Accept anything for demo purposes, or specific password "admin"
        if (pwd.length > 0) {
          err.style.opacity = '0';
          const wall = document.getElementById('auth-wall');
          wall.classList.add('fade-out');
          setTimeout(() => {
            wall.style.display = 'none';
            if (typeof window.startSplashLoader === 'function') {
              window.startSplashLoader();
            }
          }, 800);
        } else {
          err.style.opacity = '1';
        }
      }
      document.getElementById('auth-password').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') checkAuth();
      });
    </script>
  </div>
"""

import sys

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Insert the Auth Wall before Splash Loader
if '<!-- CYBERSECURITY AUTH WALL -->' not in content:
    content = content.replace('<!-- myG SPLASH LOADER -->', auth_html + '\n  <!-- myG SPLASH LOADER -->')

# Change splash loader auto-start to a manual function call
old_iife = """  <script>
  (function() {"""
new_function = """  <script>
  window.startSplashLoader = function() {"""
content = content.replace(old_iife, new_function)

# And fix the ending of the IIFE
old_end = """    }
  })();
  </script>"""
new_end = """    }
  }; // end startSplashLoader
  </script>"""
content = content.replace(old_end, new_end)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Auth wall injected successfully.")
