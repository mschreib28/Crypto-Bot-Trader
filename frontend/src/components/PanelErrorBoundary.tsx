import { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  title: string;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  message: string | null;
}

export class PanelErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, message: null };
  }

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err.message || 'Unknown error' };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    console.error(`[${this.props.title}]`, err, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div
          className="rounded-lg border border-amber-700/60 bg-amber-950/30 p-3 text-amber-200 text-xs space-y-2"
          role="alert"
        >
          <p className="font-semibold text-amber-100">{this.props.title}</p>
          <p className="text-amber-200/90 break-words">{this.state.message}</p>
          <button
            type="button"
            className="rounded bg-gray-700 px-2 py-1 text-[11px] text-gray-100 hover:bg-gray-600"
            onClick={() => this.setState({ hasError: false, message: null })}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
