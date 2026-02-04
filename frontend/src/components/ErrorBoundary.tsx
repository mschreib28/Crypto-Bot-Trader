import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center p-4">
          <div className="max-w-2xl w-full bg-gray-800 border border-red-600 rounded-lg p-6">
            <h1 className="text-2xl font-bold text-red-400 mb-4">Application Error</h1>
            <p className="text-gray-300 mb-4">
              Something went wrong. Please check the browser console for details.
            </p>
            {this.state.error && (
              <div className="bg-gray-900 rounded p-4 mb-4 overflow-auto">
                <p className="text-red-400 font-mono text-sm mb-2">Error:</p>
                <p className="text-gray-400 font-mono text-xs break-all">
                  {this.state.error.toString()}
                </p>
                {this.state.error.stack && (
                  <details className="mt-4">
                    <summary className="text-gray-400 cursor-pointer text-xs">Stack Trace</summary>
                    <pre className="text-gray-500 font-mono text-xs mt-2 whitespace-pre-wrap break-all">
                      {this.state.error.stack}
                    </pre>
                  </details>
                )}
              </div>
            )}
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null });
                window.location.reload();
              }}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
            >
              Reload Page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
