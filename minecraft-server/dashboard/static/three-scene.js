// Ambient Dynamic Three.js Background Particle & Voxel Scene
(function() {
  let scene, camera, renderer, particles, floatingCubes = [], pointLight1, pointLight2;
  let mouseX = 0, mouseY = 0;
  let targetMouseX = 0, targetMouseY = 0;
  let currentStatus = 'offline';

  function initThreeBackground() {
    const canvas = document.getElementById('three-bg-canvas');
    if (!canvas) return;

    scene = new THREE.Scene();

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.z = 20;

    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // Ambient & Point Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    scene.add(ambientLight);

    pointLight1 = new THREE.PointLight(0x10b981, 1.5, 40);
    pointLight1.position.set(-10, 10, 5);
    scene.add(pointLight1);

    pointLight2 = new THREE.PointLight(0x6366f1, 1.2, 40);
    pointLight2.position.set(10, -10, 5);
    scene.add(pointLight2);

    // Floating Magical Dust Particles
    const particleCount = 180;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);
    const scales = new Float32Array(particleCount);

    for (let i = 0; i < particleCount * 3; i += 3) {
      positions[i] = (Math.random() - 0.5) * 50;
      positions[i + 1] = (Math.random() - 0.5) * 35;
      positions[i + 2] = (Math.random() - 0.5) * 30;
      scales[i / 3] = Math.random() * 0.4 + 0.1;
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const material = new THREE.PointsMaterial({
      color: 0x10b981,
      size: 0.25,
      transparent: true,
      opacity: 0.5,
      blending: THREE.AdditiveBlending
    });

    particles = new THREE.Points(geometry, material);
    scene.add(particles);

    // Floating subtle translucent voxel cubes
    const cubeGeo = new THREE.BoxGeometry(1.2, 1.2, 1.2);
    for (let i = 0; i < 14; i++) {
      const cubeMat = new THREE.MeshStandardMaterial({
        color: i % 2 === 0 ? 0x10b981 : 0x6366f1,
        transparent: true,
        opacity: 0.12,
        roughness: 0.5,
        wireframe: i % 3 === 0
      });
      const mesh = new THREE.Mesh(cubeGeo, cubeMat);
      mesh.position.set(
        (Math.random() - 0.5) * 45,
        (Math.random() - 0.5) * 30,
        (Math.random() - 0.5) * 20 - 5
      );
      mesh.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
      mesh.userData = {
        rotSpeedX: (Math.random() - 0.5) * 0.006,
        rotSpeedY: (Math.random() - 0.5) * 0.006,
        floatSpeed: Math.random() * 0.002 + 0.001,
        floatOffset: Math.random() * Math.PI * 2,
        baseY: mesh.position.y
      };
      scene.add(mesh);
      floatingCubes.push(mesh);
    }

    // Parallax mouse tracking
    window.addEventListener('mousemove', (e) => {
      targetMouseX = (e.clientX / window.innerWidth - 0.5) * 4;
      targetMouseY = (e.clientY / window.innerHeight - 0.5) * 4;
    });

    window.addEventListener('resize', onWindowResize);

    animate();
  }

  function onWindowResize() {
    if (!renderer || !camera) return;
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  function updateStatusGlow(status) {
    currentStatus = status;
    if (!pointLight1 || !particles) return;

    if (status === 'online') {
      pointLight1.color.setHex(0x10b981);
      pointLight1.intensity = 2.0;
      particles.material.color.setHex(0x10b981);
    } else if (status === 'starting') {
      pointLight1.color.setHex(0xf59e0b);
      pointLight1.intensity = 1.6;
      particles.material.color.setHex(0xf59e0b);
    } else {
      pointLight1.color.setHex(0xef4444);
      pointLight1.intensity = 1.0;
      particles.material.color.setHex(0x6366f1);
    }
  }

  let lastFrameTime = 0;
  const fpsInterval = 1000 / 25; // 25 FPS for ultra-low CPU background ambient

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      lastFrameTime = performance.now();
      requestAnimationFrame(animate);
    }
  });

  function animate(timestamp) {
    if (document.hidden) return;
    requestAnimationFrame(animate);

    if (timestamp - lastFrameTime < fpsInterval) return;
    lastFrameTime = timestamp;

    // Smooth mouse parallax
    mouseX += (targetMouseX - mouseX) * 0.05;
    mouseY += (targetMouseY - mouseY) * 0.05;

    camera.position.x = mouseX;
    camera.position.y = -mouseY;
    camera.lookAt(0, 0, 0);

    // Rotate particles
    if (particles) {
      particles.rotation.y += 0.0008;
      particles.rotation.x += 0.0003;
    }

    // Float cubes
    const now = Date.now();
    floatingCubes.forEach(cube => {
      cube.rotation.x += cube.userData.rotSpeedX;
      cube.rotation.y += cube.userData.rotSpeedY;
      cube.position.y = cube.userData.baseY + Math.sin(now * cube.userData.floatSpeed + cube.userData.floatOffset) * 1.5;
    });

    if (renderer && scene && camera) {
      renderer.render(scene, camera);
    }
  }

  window.initThreeVisualizer = initThreeBackground;
  window.updateThreeStatus = updateStatusGlow;
})();
