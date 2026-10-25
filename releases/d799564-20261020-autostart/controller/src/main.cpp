#include "httplib.h"
#include "controller.hpp"
#include <cstdlib>
#include <iostream>
#include <set>
#include <csignal>
#ifdef _WIN32
#include <shellapi.h>
#endif

namespace {
volatile std::sig_atomic_t exiting=0;
void on_signal(int) { exiting=1; }
bool equal_token(const std::string& a, const std::string& b) {
    size_t difference=a.size()^b.size();
    for (size_t i=0;i<std::max(a.size(),b.size());++i) difference |= (i<a.size()?static_cast<unsigned char>(a[i]):0) ^ (i<b.size()?static_cast<unsigned char>(b[i]):0);
    return difference==0;
}
}
int main(int argc, char** argv) {
    try {
        std::string host="127.0.0.1", extra_port;
        plotter::fs::path jobs="controller-jobs";
#ifdef _WIN32
        int wide_count=0;
        auto wide_argv=CommandLineToArgvW(GetCommandLineW(),&wide_count);
        if (!wide_argv) throw std::runtime_error("Cannot read Unicode command line");
        std::vector<std::wstring> wide_args(wide_argv,wide_argv+wide_count);
        LocalFree(wide_argv);
        if (wide_count!=argc) throw std::runtime_error("Command line parsing mismatch");
#endif
        std::vector<std::string> extra_hosts;
        int port=8090; bool simulate=false;
        for (int i=1;i<argc;++i) {
            std::string key=argv[i];
            if (key=="--simulate") simulate=true;
            else if (key=="--help") {
                std::cout<<"plotter-controller [--simulate] [--host 127.0.0.1] [--port 8090] [--jobs-dir PATH] [--serial-port DEVICE] [--allow-host HOST]\n"
                         <<"CONTROLLER_TOKEN: bearer token; required (>=16 chars) for non-loopback listeners.\n"; return 0;
            } else {
                if (++i>=argc) throw std::runtime_error("Missing option value");
                if (key=="--host") host=argv[i]; else if (key=="--port") port=std::stoi(argv[i]);
                else if (key=="--jobs-dir") {
#ifdef _WIN32
                    jobs=wide_args[i];
#else
                    jobs=argv[i];
#endif
                } else if (key=="--serial-port") extra_port=argv[i];
                else if (key=="--allow-host") extra_hosts.push_back(argv[i]);
                else throw std::runtime_error("Unknown option: "+key);
            }
        }
        std::string token=std::getenv("CONTROLLER_TOKEN") ? std::getenv("CONTROLLER_TOKEN") : "";
        if (host!="127.0.0.1" && host!="localhost" && host!="::1" && token.size()<16)
            throw std::runtime_error("Network listening requires CONTROLLER_TOKEN of at least 16 characters");
        if (port<1 || port>65535) throw std::runtime_error("Invalid listen port");
        plotter::Controller controller(jobs,simulate,{},extra_port);
        httplib::Server server;
        std::set<std::string> allowed_hosts;
        for (const auto& name : std::vector<std::string>{host,"127.0.0.1","localhost"}) allowed_hosts.insert(name+":"+std::to_string(port));
        for (const auto& name : extra_hosts) allowed_hosts.insert(name+":"+std::to_string(port));
        allowed_hosts.insert("[::1]:"+std::to_string(port));
        server.new_task_queue=[] { return new httplib::ThreadPool(8); };
        server.set_payload_max_length(128*1024);
        server.set_read_timeout(10,0); server.set_write_timeout(10,0);
        server.set_pre_routing_handler([&](const httplib::Request& req, httplib::Response& res) {
            res.set_header("Cache-Control","no-store");
            if (!allowed_hosts.count(req.get_header_value("Host"))) { res.status=403; res.set_content("{\"error\":\"Host is not allowed\"}","application/json"); return httplib::Server::HandlerResponse::Handled; }
            if (req.has_header("Origin")) { res.status=403; res.set_content("{\"error\":\"Controller accepts service requests only\"}","application/json"); return httplib::Server::HandlerResponse::Handled; }
            if (!token.empty() && !equal_token(req.get_header_value("Authorization"),"Bearer "+token)) {
                res.status=401; res.set_content("{\"error\":\"Invalid controller token\"}","application/json"); return httplib::Server::HandlerResponse::Handled;
            }
            return httplib::Server::HandlerResponse::Unhandled;
        });
        auto handle=[&](auto fn) {
            return [&,fn](const httplib::Request& req, httplib::Response& res) {
                try {
                    plotter::Json data=plotter::Json::object();
                    if (req.method=="POST") {
                        if (req.get_header_value("Content-Type").find("application/json")!=0) { res.status=415; res.set_content("{\"error\":\"Expected application/json\"}","application/json"); return; }
                        data=plotter::Json::parse(req.body);
                        if (!data.is_object()) throw std::runtime_error("Expected JSON object");
                    }
                    res.set_content(fn(req,data).dump(),"application/json; charset=utf-8");
                } catch (const plotter::Cancelled&) { res.status=409; res.set_content("{\"error\":\"Operation cancelled\"}","application/json"); }
                catch (const plotter::Json::exception& e) { res.status=400; res.set_content(plotter::Json({{"error",e.what()}}).dump(),"application/json"); }
                catch (const std::exception& e) { res.status=409; res.set_content(plotter::Json({{"error",e.what()}}).dump(),"application/json"); }
            };
        };
        server.Get("/v1/state",handle([&](const auto&,const auto&) {return controller.state(true);}));
        server.Get("/v1/ports",handle([&](const auto&,const auto&) {return controller.ports();}));
        server.Post(R"(/v1/control/(connect|disconnect|check|start|pause|resume|stop|jog|pen|manual-pause|manual-stop))",
                    handle([&](const auto& req,const auto& data) {return controller.call(req.matches[1].str(),data);}));
        server.Post(R"(/v1/jobs/([a-f0-9]{32})/(begin|chunk|seal))",
                    handle([&](const auto& req,const auto& data) {return controller.upload(req.matches[1].str(),req.matches[2].str(),data);}));
        server.set_error_handler([](const auto&,auto& res) { if (res.body.empty()) res.set_content(plotter::Json({{"error","Controller endpoint or request not accepted"}}).dump(),"application/json"); });
        std::cout<<"C++ controller http://"<<host<<":"<<port<<" | "<<(simulate?"simulation":"serial")<<std::endl;
        if (!server.bind_to_port(host,port)) throw std::runtime_error("Cannot bind controller listener");
        std::signal(SIGINT,on_signal); std::signal(SIGTERM,on_signal);
        std::atomic<bool> listener_done{false};
        std::thread listener([&] { server.listen_after_bind(); listener_done=true; });
        while (!exiting && !listener_done) std::this_thread::sleep_for(std::chrono::milliseconds(100));
        server.stop(); listener.join();
    } catch (const std::exception& e) { std::cerr<<e.what()<<std::endl; return 1; }
}
