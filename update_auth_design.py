import re

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Define the new highly-detailed SVG design replacing the old background and rings
new_design = """
    <!-- Exact Design Match SVG Background & Animation -->
    <div class="auth-circuit-bg">
      <svg width="100%" height="100%" viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <filter id="cyanGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="4" result="blur1"/>
            <feGaussianBlur stdDeviation="8" result="blur2"/>
            <feGaussianBlur stdDeviation="15" result="blur3"/>
            <feMerge>
              <feMergeNode in="blur3"/>
              <feMergeNode in="blur2"/>
              <feMergeNode in="blur1"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
          
          <filter id="faintGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur"/>
            <feMerge>
              <feMergeNode in="blur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>

          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <rect width="40" height="40" fill="none" stroke="rgba(0, 229, 255, 0.07)" stroke-width="1"/>
          </pattern>
        </defs>

        <!-- Background Grid -->
        <rect width="100%" height="100%" fill="#030814" />
        <rect width="100%" height="100%" fill="url(#grid)" />
        
        <!-- Subtle background squares -->
        <g fill="rgba(0, 229, 255, 0.03)">
          <rect x="50" y="80" width="80" height="80" />
          <rect x="850" y="400" width="120" height="120" />
          <rect x="750" y="50" width="80" height="80" />
          <rect x="150" y="450" width="40" height="40" />
        </g>

        <!-- Central Hub Glow -->
        <circle cx="500" cy="300" r="150" fill="rgba(0, 229, 255, 0.05)" filter="url(#cyanGlow)" />

        <!-- Circuit Lines (Left) -->
        <g stroke="#00e5ff" fill="none" stroke-linejoin="bevel" filter="url(#cyanGlow)">
          <!-- Top Line -->
          <path d="M0 160 H 200 L 280 240 H 340" stroke-width="5"/>
          <circle cx="340" cy="240" r="6" fill="#00e5ff"/>
          
          <!-- Middle Top Line -->
          <path d="M0 220 H 120 L 220 120 H 260" stroke-width="4"/>
          <circle cx="260" cy="120" r="5" fill="#00e5ff"/>
          
          <!-- Core Input Line -->
          <path d="M0 300 H 250 L 300 250 H 350" stroke-width="7"/>
          <circle cx="350" cy="250" r="8" fill="#00e5ff"/>

          <!-- Middle Bottom Line -->
          <path d="M0 380 H 220 L 300 300 H 330" stroke-width="4"/>
          <circle cx="330" cy="300" r="5" fill="#00e5ff"/>

          <!-- Bottom Line -->
          <path d="M0 450 H 180 L 280 350 H 350" stroke-width="5"/>
          <circle cx="350" cy="350" r="7" fill="#00e5ff"/>
          
          <!-- Faint sub-lines -->
          <path d="M0 260 H 80 L 120 300 H 180" stroke-width="1.5" stroke="rgba(0, 229, 255, 0.5)" filter="url(#faintGlow)"/>
          <path d="M0 500 H 100 L 150 450 H 220" stroke-width="1.5" stroke="rgba(0, 229, 255, 0.5)" filter="url(#faintGlow)"/>
        </g>

        <!-- Circuit Lines (Right - Mirrored) -->
        <g stroke="#00e5ff" fill="none" stroke-linejoin="bevel" filter="url(#cyanGlow)">
          <!-- Top Line -->
          <path d="M1000 160 H 800 L 720 240 H 660" stroke-width="5"/>
          <circle cx="660" cy="240" r="6" fill="#00e5ff"/>
          
          <!-- Middle Top Line -->
          <path d="M1000 220 H 880 L 780 120 H 740" stroke-width="4"/>
          <circle cx="740" cy="120" r="5" fill="#00e5ff"/>
          
          <!-- Core Input Line -->
          <path d="M1000 300 H 750 L 700 250 H 650" stroke-width="7"/>
          <circle cx="650" cy="250" r="8" fill="#00e5ff"/>

          <!-- Middle Bottom Line -->
          <path d="M1000 380 H 780 L 700 300 H 670" stroke-width="4"/>
          <circle cx="670" cy="300" r="5" fill="#00e5ff"/>

          <!-- Bottom Line -->
          <path d="M1000 450 H 820 L 720 350 H 650" stroke-width="5"/>
          <circle cx="650" cy="350" r="7" fill="#00e5ff"/>

          <!-- Faint sub-lines -->
          <path d="M1000 260 H 920 L 880 300 H 820" stroke-width="1.5" stroke="rgba(0, 229, 255, 0.5)" filter="url(#faintGlow)"/>
          <path d="M1000 500 H 900 L 850 450 H 780" stroke-width="1.5" stroke="rgba(0, 229, 255, 0.5)" filter="url(#faintGlow)"/>
        </g>

        <!-- Dynamic Concentric Rings -->
        <g transform="translate(500, 300)">
          <!-- Outer Segmented Ring -->
          <circle cx="0" cy="0" r="160" stroke="#00e5ff" stroke-width="2" fill="none" stroke-dasharray="10 5" opacity="0.3">
            <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="20s" repeatCount="indefinite" />
          </circle>
          
          <!-- Gear Teeth Ring -->
          <circle cx="0" cy="0" r="140" stroke="#00e5ff" stroke-width="12" fill="none" stroke-dasharray="4 8" filter="url(#faintGlow)">
            <animateTransform attributeName="transform" type="rotate" from="360" to="0" dur="30s" repeatCount="indefinite" />
          </circle>
          
          <!-- Bright Solid Ring -->
          <circle cx="0" cy="0" r="120" stroke="#00e5ff" stroke-width="3" fill="none" filter="url(#cyanGlow)" />
          
          <!-- Inner Dashed Ring -->
          <circle cx="0" cy="0" r="100" stroke="#00e5ff" stroke-width="4" fill="none" stroke-dasharray="25 15" filter="url(#faintGlow)">
            <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="15s" repeatCount="indefinite" />
          </circle>
          
          <!-- Core Ring -->
          <circle cx="0" cy="0" r="75" stroke="#00e5ff" stroke-width="2" fill="none" stroke-dasharray="2 4" />
          
          <!-- Lock Background -->
          <circle cx="0" cy="0" r="60" fill="rgba(0, 229, 255, 0.15)" filter="url(#cyanGlow)" />
        </g>
      </svg>
    </div>
    
    <div class="auth-form" style="position:absolute; bottom:15%; z-index: 10;">
"""

# Extract everything before the old background and after it
start_idx = content.find('<div class="auth-bg"></div>')
end_idx = content.find('<h2 class="auth-title">System Locked</h2>')

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_design + content[end_idx:]
    
    # We also need to add CSS for the new background positioning and remove the old ring css
    css_start = new_content.find('/* Circuit Background Pattern */')
    css_end = new_content.find('/* Auth Form */')
    
    new_css = """/* New Authentic Circuit Background */
      .auth-circuit-bg {
        position: absolute;
        inset: 0;
        z-index: 1;
        width: 100%;
        height: 100%;
      }
      
      .auth-lock-icon {
        position: absolute;
        width: 50px;
        height: 50px;
        color: #00e5ff;
        filter: drop-shadow(0 0 12px #00e5ff);
        z-index: 3;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
      }
      
      """
      
    if css_start != -1 and css_end != -1:
        new_content = new_content[:css_start] + new_css + new_content[css_end:]
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Design successfully updated to match reference image.")
else:
    print("Could not find insertion points.")
