import re

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = '  <!-- Close-up Earth Orbit Animation -->'
end_marker = '</body>'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

new_animation = """  <!-- Data Plexus Network Animation (Light Theme) -->
  <script>
  (function () {
    const canvas = document.getElementById('bg-canvas');
    if (!canvas || typeof THREE === 'undefined') return;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 0);

    const scene = new THREE.Scene();
    
    // Very light fog to fade out distant nodes
    scene.fog = new THREE.FogExp2(0xf0f4f8, 0.002);

    const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.z = 250;

    const group = new THREE.Group();
    scene.add(group);

    const particleCount = 200;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);
    const velocities = [];

    const r = 500;
    for (let i = 0; i < particleCount; i++) {
      positions[i * 3]     = (Math.random() * r - r / 2);
      positions[i * 3 + 1] = (Math.random() * r - r / 2);
      positions[i * 3 + 2] = (Math.random() * r - r / 2);
      velocities.push({
        x: (Math.random() - 0.5) * 0.4,
        y: (Math.random() - 0.5) * 0.4,
        z: (Math.random() - 0.5) * 0.4
      });
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    
    // Draw dots
    const pMaterial = new THREE.PointsMaterial({
      color: 0x1e6eff,
      size: 4,
      transparent: true,
      opacity: 0.6
    });
    const particles = new THREE.Points(geometry, pMaterial);
    group.add(particles);

    // Draw lines
    const linesMaterial = new THREE.LineBasicMaterial({
      color: 0x00a8cc,
      transparent: true,
      opacity: 0.15
    });
    const linesGeometry = new THREE.BufferGeometry();
    const lineMesh = new THREE.LineSegments(linesGeometry, linesMaterial);
    group.add(lineMesh);

    window.addEventListener('resize', () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

    let mouseX = 0, mouseY = 0;
    document.addEventListener('mousemove', (e) => {
      mouseX = (e.clientX - window.innerWidth / 2) * 0.1;
      mouseY = (e.clientY - window.innerHeight / 2) * 0.1;
    });

    function animate() {
      requestAnimationFrame(animate);
      
      const pos = geometry.attributes.position.array;
      
      // Move particles
      for (let i = 0; i < particleCount; i++) {
        pos[i * 3]     += velocities[i].x;
        pos[i * 3 + 1] += velocities[i].y;
        pos[i * 3 + 2] += velocities[i].z;

        // Bounce off bounds
        if (pos[i * 3] < -r/2 || pos[i * 3] > r/2) velocities[i].x *= -1;
        if (pos[i * 3 + 1] < -r/2 || pos[i * 3 + 1] > r/2) velocities[i].y *= -1;
        if (pos[i * 3 + 2] < -r/2 || pos[i * 3 + 2] > r/2) velocities[i].z *= -1;
      }
      geometry.attributes.position.needsUpdate = true;
      
      // Connect close particles with lines
      const linePositions = [];
      for (let i = 0; i < particleCount; i++) {
        for (let j = i + 1; j < particleCount; j++) {
          const dx = pos[i * 3] - pos[j * 3];
          const dy = pos[i * 3 + 1] - pos[j * 3 + 1];
          const dz = pos[i * 3 + 2] - pos[j * 3 + 2];
          const dist = Math.sqrt(dx*dx + dy*dy + dz*dz);
          
          if (dist < 70) {
            linePositions.push(
              pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2],
              pos[j * 3], pos[j * 3 + 1], pos[j * 3 + 2]
            );
          }
        }
      }
      
      linesGeometry.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));

      // Global rotation and mouse parallax
      group.rotation.y += 0.0005;
      group.rotation.x += 0.0002;
      
      camera.position.x += (mouseX - camera.position.x) * 0.02;
      camera.position.y += (-mouseY - camera.position.y) * 0.02;
      camera.lookAt(scene.position);

      renderer.render(scene, camera);
    }
    
    animate();
  })();
  </script>
</body>"""

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_animation + content[end_idx + 7:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Replaced globe animation with Light Plexus animation.")
