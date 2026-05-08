// ═════════════════════════════════════════════════════════════════════════════
//  PRODUCTION JENKINSFILE
//  Pipeline: Python App → Test → Docker → ArgoCD (GitOps) → K8s
//  Flow: Build → Test → Sonar → Docker → Update Helm values → ArgoCD sync
// ═════════════════════════════════════════════════════════════════════════════

pipeline {

  // ── Agent: Kubernetes pod with Python + Docker + ArgoCD CLI ──────────────
  agent {
    kubernetes {
      yaml """
        apiVersion: v1
        kind: Pod
        spec:
          serviceAccountName: jenkins-agent
          containers:
          - name: python
            image: python:3.12-slim
            command: ['cat']
            tty: true
            resources:
              requests: { memory: "512Mi", cpu: "300m" }
              limits:   { memory: "1Gi",   cpu: "800m" }

          - name: docker
            image: docker:24-dind
            securityContext:
              privileged: true
            volumeMounts:
            - name: docker-sock
              mountPath: /var/run/docker.sock

          - name: argocd
            image: argoproj/argocd:v2.10.0
            command: ['cat']
            tty: true

          - name: helm
            image: alpine/helm:3.14.0
            command: ['cat']
            tty: true

          volumes:
          - name: docker-sock
            hostPath:
              path: /var/run/docker.sock
      """
    }
  }

  // ── Environment ───────────────────────────────────────────────────────────
  environment {
    APP_NAME        = 'python-cicd-app'
    DOCKER_REGISTRY = 'registry.company.com'
    DOCKER_IMAGE    = "${DOCKER_REGISTRY}/${APP_NAME}"
    GIT_COMMIT_SHA  = "${env.GIT_COMMIT ? env.GIT_COMMIT[0..7] : 'unknown'}"
    IMAGE_TAG       = "${BUILD_NUMBER}-${GIT_COMMIT_SHA}"
    HELM_CHART_PATH = 'helm/python-app'
    ARGOCD_SERVER   = 'argocd.company.com'
    SONAR_PROJECT   = 'python-cicd-app'
    SLACK_CHANNEL   = '#deployments'

    // Credentials from Jenkins store
    DOCKER_CREDS   = credentials('docker-registry-creds')
    SONAR_TOKEN    = credentials('sonarqube-token')
    ARGOCD_TOKEN   = credentials('argocd-auth-token')
    GIT_CREDS      = credentials('github-token')
  }

  // ── Options ───────────────────────────────────────────────────────────────
  options {
    timeout(time: 40, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
    disableConcurrentBuilds()
    ansiColor('xterm')
    timestamps()
  }

  // ── Parameters ────────────────────────────────────────────────────────────
  parameters {
    choice(name: 'DEPLOY_ENV',
           choices: ['auto', 'dev', 'staging', 'production'],
           description: 'auto = branch-based routing')
    booleanParam(name: 'SKIP_TESTS',      defaultValue: false, description: 'Skip tests (emergency only)')
    booleanParam(name: 'SKIP_SONAR',      defaultValue: false, description: 'Skip SonarQube scan')
    booleanParam(name: 'FORCE_ARGOCD_SYNC', defaultValue: false, description: 'Force ArgoCD sync even if in sync')
  }

  triggers {
    pollSCM('H/5 * * * *')
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // STAGES
  // ═══════════════════════════════════════════════════════════════════════════
  stages {

    // ────────────────────────────────────────────────────────────────────────
    // 1. CHECKOUT
    // ────────────────────────────────────────────────────────────────────────
    stage('Checkout') {
      steps {
        checkout scm
        script {
          currentBuild.displayName = "#${BUILD_NUMBER} | ${IMAGE_TAG} | ${env.BRANCH_NAME}"
          env.RESOLVED_ENV = resolveEnvironment()
          echo "Building for environment: ${env.RESOLVED_ENV}"
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 2. SETUP Python environment
    // ────────────────────────────────────────────────────────────────────────
    stage('Setup') {
      steps {
        container('python') {
          dir('app') {
            sh '''
              pip install --upgrade pip --quiet
              pip install -r requirements.txt --quiet
              echo "Python setup complete"
              python --version
              pip list | head -20
            '''
          }
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 3. PARALLEL — Tests + Lint + Security
    // ────────────────────────────────────────────────────────────────────────
    stage('Quality Checks') {
      when {
        expression { return !params.SKIP_TESTS }
      }
      parallel {

        stage('Unit Tests') {
          steps {
            container('python') {
              dir('app') {
                sh '''
                  pytest tests/ \
                    -v \
                    --tb=short \
                    --cov=app \
                    --cov-report=xml:coverage.xml \
                    --cov-report=term-missing \
                    --cov-fail-under=80 \
                    --junitxml=test-results.xml
                '''
              }
            }
          }
          post {
            always {
              dir('app') {
                junit 'test-results.xml'
                publishCoverage adapters: [coberturaAdapter('coverage.xml')],
                                sourceFileResolver: sourceFiles('STORE_LAST_BUILD')
              }
            }
          }
        }

        stage('Lint & Format') {
          steps {
            container('python') {
              dir('app') {
                sh '''
                  pip install flake8 black isort --quiet

                  echo "Running Black (format check)..."
                  black --check --diff . || true

                  echo "Running Flake8 (style check)..."
                  flake8 . \
                    --max-line-length=100 \
                    --exclude=.git,__pycache__,.venv \
                    --format=pylint \
                    --output-file=flake8-report.txt || true

                  echo "Running isort (import order check)..."
                  isort --check-only --diff . || true

                  cat flake8-report.txt || true
                '''
              }
            }
          }
        }

        stage('Security Scan') {
          steps {
            container('python') {
              dir('app') {
                sh '''
                  pip install bandit safety --quiet

                  echo "Running Bandit (static security analysis)..."
                  bandit -r . \
                    -x tests \
                    --format xml \
                    --output bandit-report.xml \
                    --severity-level medium || true

                  echo "Running Safety (dependency vulnerability check)..."
                  safety check \
                    -r requirements.txt \
                    --json > safety-report.json || true

                  cat safety-report.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
vulns = data.get('vulnerabilities', [])
if vulns:
    print(f'WARNING: {len(vulns)} vulnerabilities found!')
    for v in vulns:
        print(f'  - {v[\"package_name\"]} {v[\"analyzed_version\"]}: {v[\"advisory\"][:80]}')
else:
    print('No vulnerabilities found.')
  " || true
                '''
              }
            }
          }
          post {
            always {
              dir('app') {
                archiveArtifacts artifacts: 'bandit-report.xml,safety-report.json',
                                 allowEmptyArchive: true
              }
            }
          }
        }

      } // end parallel
    }

    // ────────────────────────────────────────────────────────────────────────
    // 4. SONARQUBE ANALYSIS
    // ────────────────────────────────────────────────────────────────────────
    stage('SonarQube') {
      when {
        expression { return !params.SKIP_SONAR && !params.SKIP_TESTS }
      }
      steps {
        container('python') {
          sh '''
            pip install pysonar-scanner --quiet || true

            sonar-scanner \
              -Dsonar.projectKey=${SONAR_PROJECT} \
              -Dsonar.projectName="${APP_NAME}" \
              -Dsonar.projectVersion=${IMAGE_TAG} \
              -Dsonar.sources=app \
              -Dsonar.tests=app/tests \
              -Dsonar.python.coverage.reportPaths=app/coverage.xml \
              -Dsonar.python.xunit.reportPath=app/test-results.xml \
              -Dsonar.login=${SONAR_TOKEN} \
              -Dsonar.host.url=https://sonar.company.com
          '''
        }
      }
      post {
        always {
          timeout(time: 5, unit: 'MINUTES') {
            waitForQualityGate abortPipeline: true
          }
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 5. DOCKER BUILD & PUSH
    // ────────────────────────────────────────────────────────────────────────
    stage('Docker Build & Push') {
      steps {
        container('docker') {
          dir('app') {
            sh """
              BUILD_DATE=\$(date -u +'%Y-%m-%dT%H:%M:%SZ')

              echo "=== Building Docker image ==="
              docker build \\
                --build-arg APP_VERSION=${IMAGE_TAG} \\
                --build-arg GIT_COMMIT=${env.GIT_COMMIT} \\
                --build-arg BUILD_DATE=\${BUILD_DATE} \\
                --cache-from ${DOCKER_IMAGE}:latest \\
                -t ${DOCKER_IMAGE}:${IMAGE_TAG} \\
                -t ${DOCKER_IMAGE}:latest \\
                .

              echo "=== Logging in to registry ==="
              echo ${DOCKER_CREDS_PSW} | docker login ${DOCKER_REGISTRY} \\
                -u ${DOCKER_CREDS_USR} --password-stdin

              echo "=== Pushing images ==="
              docker push ${DOCKER_IMAGE}:${IMAGE_TAG}
              docker push ${DOCKER_IMAGE}:latest

              echo "=== Image size ==="
              docker images ${DOCKER_IMAGE}:${IMAGE_TAG} \\
                --format "table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}"

              echo "=== Cleaning up ==="
              docker rmi ${DOCKER_IMAGE}:${IMAGE_TAG} || true
              docker rmi ${DOCKER_IMAGE}:latest || true
            """
          }
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 6. UPDATE HELM VALUES (GitOps: update image tag in git → ArgoCD picks up)
    // ────────────────────────────────────────────────────────────────────────
    stage('Update Helm Values') {
      steps {
        script {
          def valuesFile = "helm/python-app/values-${env.RESOLVED_ENV}.yaml"
          sh """
            git config user.email "jenkins@company.com"
            git config user.name  "Jenkins CI"

            echo "Updating image tag in ${valuesFile} to ${IMAGE_TAG}..."

            # Use sed to update image.tag in the values file
            # In production, consider using yq for proper YAML editing
            sed -i 's|tag:.*|tag: ${IMAGE_TAG}|g' ${valuesFile}

            git add ${valuesFile}
            git commit -m "ci: update ${env.RESOLVED_ENV} image tag to ${IMAGE_TAG} [skip ci]"

            git push https://${GIT_CREDS_USR}:${GIT_CREDS_PSW}@github.com/your-org/python-cicd-project.git HEAD:${env.BRANCH_NAME}

            echo "Helm values updated and pushed to git"
          """
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 7. ARGOCD SYNC (Dev & Staging — auto)
    // ────────────────────────────────────────────────────────────────────────
    stage('ArgoCD Sync — Dev') {
      when {
        expression { return env.RESOLVED_ENV == 'dev' }
      }
      steps {
        container('argocd') {
          sh """
            argocd login ${ARGOCD_SERVER} \
              --auth-token ${ARGOCD_TOKEN} \
              --insecure \
              --grpc-web

            echo "Syncing ${APP_NAME}-dev..."
            argocd app sync ${APP_NAME}-dev \
              --force \
              --prune \
              --timeout 300

            echo "Waiting for health..."
            argocd app wait ${APP_NAME}-dev \
              --health \
              --timeout 300

            echo "=== App status ==="
            argocd app get ${APP_NAME}-dev
          """
        }
      }
    }

    stage('ArgoCD Sync — Staging') {
      when {
        expression { return env.RESOLVED_ENV == 'staging' }
      }
      steps {
        container('argocd') {
          sh """
            argocd login ${ARGOCD_SERVER} \
              --auth-token ${ARGOCD_TOKEN} \
              --insecure \
              --grpc-web

            echo "Syncing ${APP_NAME}-staging..."
            argocd app sync ${APP_NAME}-staging \
              --force \
              --prune \
              --timeout 300

            argocd app wait ${APP_NAME}-staging \
              --health \
              --timeout 300

            argocd app get ${APP_NAME}-staging
          """
        }
      }
      post {
        success {
          slackSend channel: "${SLACK_CHANNEL}",
                    color: 'good',
                    message: "✅ *Staging Deploy Success*\n`${APP_NAME}:${IMAGE_TAG}` is live on staging.\nApprove for production? ${env.BUILD_URL}input"
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 8. SMOKE TESTS post-staging deploy
    // ────────────────────────────────────────────────────────────────────────
    stage('Smoke Tests') {
      when {
        expression { return env.RESOLVED_ENV == 'staging' }
      }
      steps {
        retry(3) {
          sh '''
            sleep 15
            echo "Testing staging health endpoint..."
            curl --fail --silent \
              https://staging-app.company.com/health/ready | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['status']=='UP', f'Health check failed: {d}'
print('Health check PASSED:', d['status'])
"
            echo "Testing version endpoint..."
            curl --fail --silent \
              https://staging-app.company.com/api/v1/version | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('Version:', d['version'])
print('Env:', d['env'])
assert d['env'] == 'staging'
print('Smoke tests PASSED')
"
          '''
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 9. APPROVAL GATE before production
    // ────────────────────────────────────────────────────────────────────────
    stage('Approve Production') {
      when {
        expression { return env.RESOLVED_ENV == 'production' }
      }
      steps {
        script {
          def approver = input(
            message: "🚀 Deploy ${APP_NAME}:${IMAGE_TAG} to PRODUCTION?",
            ok: 'Approve & Deploy',
            submitter: 'release-managers,tech-leads',
            parameters: [
              string(name: 'COMMENT', defaultValue: '', description: 'Reason / ticket number')
            ]
          )
          currentBuild.description += " | Approved: ${approver}"
          echo "Production deploy approved. Proceeding..."
        }
      }
    }

    // ────────────────────────────────────────────────────────────────────────
    // 10. ARGOCD SYNC — Production (manual trigger via ArgoCD)
    // ────────────────────────────────────────────────────────────────────────
    stage('ArgoCD Sync — Production') {
      when {
        expression { return env.RESOLVED_ENV == 'production' }
      }
      steps {
        container('argocd') {
          sh """
            argocd login ${ARGOCD_SERVER} \
              --auth-token ${ARGOCD_TOKEN} \
              --insecure \
              --grpc-web

            echo "Syncing ${APP_NAME}-prod..."
            argocd app sync ${APP_NAME}-prod \
              --force \
              --prune \
              --timeout 600

            argocd app wait ${APP_NAME}-prod \
              --health \
              --timeout 600

            argocd app get ${APP_NAME}-prod
          """
        }
      }
      post {
        success {
          sh """
            git tag -a "release-${IMAGE_TAG}" \
              -m "Production release ${IMAGE_TAG} | Build #${BUILD_NUMBER}"
            git push https://${GIT_CREDS_USR}:${GIT_CREDS_PSW}@github.com/your-org/python-cicd-project.git \
              "release-${IMAGE_TAG}"
          """
          slackSend channel: "${SLACK_CHANNEL}",
                    color: 'good',
                    message: "🚀 *PRODUCTION DEPLOY SUCCESS*\n`${APP_NAME}:${IMAGE_TAG}` is live in production!"
        }
      }
    }

  } // end stages

  // ─────────────────────────────────────────────────────────────────────────
  // POST
  // ─────────────────────────────────────────────────────────────────────────
  post {
    failure {
      slackSend channel: "${SLACK_CHANNEL}",
                color: 'danger',
                message: "❌ *Pipeline FAILED*\nJob: `${env.JOB_NAME}` | Build: `#${env.BUILD_NUMBER}`\nStage: `${env.STAGE_NAME}`\nSee: ${env.BUILD_URL}"
    }
    always {
      cleanWs()
    }
  }

} // end pipeline


// ─────────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────────
def resolveEnvironment() {
  if (params.DEPLOY_ENV != 'auto') return params.DEPLOY_ENV
  switch (env.BRANCH_NAME) {
    case 'develop': return 'dev'
    case 'main':    return 'staging'
    case ~/release\/.*/: return 'production'
    default:        return 'dev'
  }
}
