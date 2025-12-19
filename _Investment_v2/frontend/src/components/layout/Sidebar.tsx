import React from 'react';
import { LayoutDashboard, Focus, X } from 'lucide-react';
import { Button } from "@/components/ui/button";

interface SidebarProps {
    isOpen: boolean;
    onClose: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
    return (
        <div
            className={`
                fixed top-0 left-0 h-full w-96 
                bg-black/80 backdrop-blur-2xl border-r border-white/10 
                z-[60] transition-transform duration-300 ease-in-out
                flex flex-col
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}
            `}
        >
            {/* Header */}
            <div className="h-14 flex items-center justify-between px-6 border-b border-white/10 flex-none">
                <span className="font-medium text-sm text-white/90 tracking-wide">Navigation</span>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    className="h-8 w-8 text-white/50 hover:text-white hover:bg-white/10"
                >
                    <X className="h-4 w-4" />
                </Button>
            </div>

            {/* Menu Items */}
            <div className="flex-1 p-4 space-y-2">
                <Button
                    variant="ghost"
                    className="w-full justify-start text-white/70 hover:text-white hover:bg-white/5 h-12"
                >
                    <LayoutDashboard className="h-5 w-5 mr-3" />
                    Dashboard
                </Button>

                <Button
                    variant="ghost"
                    className="w-full justify-start text-white/70 hover:text-white hover:bg-white/5 h-12"
                >
                    <Focus className="h-5 w-5 mr-3" />
                    Focus
                </Button>
            </div>
        </div>
    );
}
