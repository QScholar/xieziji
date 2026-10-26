#include "controller.hpp"
#include "picosha2.h"
#include <algorithm>
#include <cmath>
#include <regex>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif

namespace plotter {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
bool starts(const std::string& value, const std::string& prefix) { return value.rfind(prefix, 0) == 0; }
double numeric(const Json& data, const char* key, double low, double high) {
    require(data.contains(key) && data[key].is_number(), "Missing numeric parameter");
    double value = data[key].get<double>();
    require(std::isfinite(value) && value >= low && value <= high, "Numeric parameter out of range"); return value;
}
void atomic_json(const fs::path& path, const Json& value) {
    auto temp = path; temp += ".tmp";
    { std::ofstream f(temp, std::ios::binary | std::ios::trunc); f << value.dump(2); f.flush(); require(bool(f), "Cannot save controller state"); }
#ifdef _WIN32
    // Windows rename does not replace an existing file. A missing state file
    // never means a task can replay: execution is claimed by a separate marker.
    std::error_code ec; fs::remove(path, ec);
#endif
    fs::rename(temp, path);
}
void claim_job(const fs::path& folder) {
    auto path = folder / "consumed";
    const char* message = "claimed; never automatically replay\n";
#ifdef _WIN32
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, nullptr);
    require(file != INVALID_HANDLE_VALUE,"Task already claimed or claim file cannot be created");
    DWORD written = 0;
    bool ok = WriteFile(file,message,static_cast<DWORD>(std::char_traits<char>::length(message)),&written,nullptr)
              && written == std::char_traits<char>::length(message) && FlushFileBuffers(file);
    CloseHandle(file); require(ok,"Cannot persist task claim");
#else
    int fd = ::open(path.c_str(),O_WRONLY | O_CREAT | O_EXCL,0600);
    require(fd>=0,"Task already claimed or claim file cannot be created");
    auto size = std::char_traits<char>::length(message);
    bool ok = ::write(fd,message,size)==static_cast<ssize_t>(size) && ::fsync(fd)==0;
    ::close(fd); require(ok,"Cannot persist task claim");
    int directory=::open(folder.c_str(),O_RDONLY | O_DIRECTORY);
    require(directory>=0,"Cannot open task directory"); ok=::fsync(directory)==0; ::close(directory);
    require(ok,"Cannot persist task directory");
#endif
}
std::vector<double> values(const std::string& text) {
    std::vector<double> result; std::istringstream stream(text); std::string part;
    while (std::getline(stream, part, ',')) {
        size_t used = 0; double value = std::stod(part, &used);
        require(used == part.size() && std::isfinite(value), "Invalid controller coordinates"); result.push_back(value);
    }
    return result;
}
}

Controller::Controller(fs::path jobs, bool simulation, Factory serial_factory, std::string additional_port)
    : root(fs::absolute(jobs)), simulate(simulation), factory(std::move(serial_factory)), extra_port(std::move(additional_port)) {
    fs::create_directories(root);
    current = {{"connected",false},{"mode",simulate ? "simulation" : "serial"},{"phase","disconnected"},
               {"status",""},{"port",""},{"job_id",nullptr},{"acknowledged",0},{"total",0},{"error",nullptr},
               {"operation_type",nullptr},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr},
               {"motion_revision",0},{"last_action",""},{"controller","cpp"},{"protocol_version",1}};
}
Controller::~Controller() {
    try { std::lock_guard<std::mutex> lock(tx_mutex); stopped = true; paused = false; if (serial && busy) serial->write("!"); } catch (...) {}
    if (worker.joinable()) worker.join();
}
void Controller::update(const Json& fields) { std::lock_guard<std::mutex> lock(state_mutex); current.update(fields); }
Json Controller::state(bool refresh) {
    if (refresh && api_mutex.try_lock()) {
        std::unique_lock<std::mutex> guard(api_mutex, std::adopt_lock);
        auto snapshot = state();
        if (!busy && snapshot["connected"] == true && snapshot["phase"] != "failed" && Clock::now() - last_poll > 500ms) {
            try { status(); last_poll = Clock::now(); } catch (const std::exception& e) { failure(e.what(), false); }
        }
    }
    std::lock_guard<std::mutex> lock(state_mutex); return current;
}
Json Controller::ports() {
    Json result = Json::array();
    if (simulate) result.push_back({{"device","SIMULATOR"},{"description","C++ simulated GRBL"}});
    else {
        for (auto& p : serial_ports()) result.push_back({{"device",p.device},{"description",p.description}});
        if (!extra_port.empty()) result.push_back({{"device",extra_port},{"description","Explicit serial device"}});
    }
    return {{"ports",result}};
}
void Controller::cancelled() { if (stopped) throw Cancelled{}; }
void Controller::record(const char* direction, const std::string& raw) {
    std::lock_guard<std::mutex> lock(log_mutex);
    if (log.is_open()) {
        static const char* digits = "0123456789abcdef";
        std::string hex; for (unsigned char c : raw) { hex += digits[c >> 4]; hex += digits[c & 15]; }
        log << Json({{"time",std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()},
                     {"direction",direction},{"hex",hex}}).dump() << '\n'; log.flush();
    }
}
void Controller::write(const std::string& raw, bool realtime) {
    std::lock_guard<std::mutex> lock(tx_mutex);
    if (!realtime) cancelled();
    require(bool(serial), "Serial device is not connected"); record("tx", raw); serial->write(raw);
}
std::string Controller::read() {
    cancelled(); auto line = serial->read_line(); record("rx", line); cancelled();
    if (starts(line, "<")) {
        Json fields = {{"status",line},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr}};
        std::smatch match; const std::regex position("MPos:([-0-9.]+),([-0-9.]+),([-0-9.]+)");
        if (std::regex_search(line, match, position)) {
            auto p = values(match[1].str() + "," + match[2].str() + "," + match[3].str());
            fields["position"] = p; fields["pen_top"] = std::abs(p[2]) <= .05;
            fields["pen"] = std::abs(p[2]) <= .05 ? "up" : std::abs(p[2]-10) <= .05 ? "down" : "unknown";
        }
        update(fields);
    }
    if (starts(line, "error:") || starts(line, "ALARM:")) throw SerialError(line);
    return line;
}
std::string Controller::status() {
    write("?"); auto deadline = Clock::now() + 4s;
    while (Clock::now() < deadline) { auto line = read(); if (starts(line, "<")) return line; }
    throw SerialError("Controller status timeout");
}
std::vector<std::string> Controller::command(const std::string& line, bool running, bool manual) {
    require(line.size() + 1 <= 78, "G-code line too long");
    // Pause and cancellation share the write lock: no new motion can slip past
    // an accepted hold/cancel request, even between the caller's checks.
    for (;;) {
        cancelled();
        {
            std::lock_guard<std::mutex> lock(tx_mutex); cancelled();
            if (manual && manual_cancel) return {};
            if (!running || !paused) { record("tx", line + "\n"); serial->write(line + "\n"); break; }
        }
        status(); std::this_thread::sleep_for(20ms);
    }
    auto deadline = Clock::now() + 30s, poll = Clock::now(); std::vector<std::string> replies;
    for (;;) {
        auto reply = read();
        if (!reply.empty()) { replies.push_back(reply); require(replies.size() <= 4096, "Too many controller responses"); if (reply == "ok") return replies; }
        auto now = Clock::now();
        if (running && now-poll > 500ms) { write("?"); poll = now; }
        if (running && paused) deadline = now + 30s;
        if (now > deadline) throw SerialError("Command acknowledgement timeout");
    }
}
Json Controller::check(const Json& metadata) {
    auto report = status(); require(starts(report, "<Idle"), "Machine must be Idle");
    auto p = state()["position"]; require(p.is_array() && p.size() == 3 && std::abs(p[2].get<double>()) <= .01, "Raise pen to Z0 first");
    auto modal = command("$G");
    require(std::any_of(modal.begin(), modal.end(), [](const auto& s){return std::regex_search(s, std::regex("\\bG54\\b"));}), "Expected G54 work coordinates");
    auto parameters = command("$#"); std::map<std::string,std::vector<double>> offsets;
    for (auto& line : parameters) { std::smatch m; if (std::regex_match(line,m,std::regex("\\[(G54|G92|TLO):([0-9.,-]+)\\]"))) offsets[m[1]] = values(m[2]); }
    require(offsets["G54"].size() == 3 && offsets["TLO"].size() == 1 && offsets["G92"].size() == 3, "Missing coordinate offsets");
    for (auto key : {"G54","TLO"}) for (double value : offsets[key]) require(std::abs(value) <= .01, "Nonzero work/tool offset");
    require(std::abs(offsets["G92"][2]) <= .01, "Nonzero Z offset");
    auto replies = command("$$"); std::map<int,double> settings;
    for (auto& line : replies) { std::smatch m; if (std::regex_match(line,m,std::regex("\\$([0-9]+)=([0-9.]+)"))) settings[std::stoi(m[1])] = std::stod(m[2]); }
    auto paper = metadata.value("machine_paper",metadata.value("paper",Json::array({210,297})));
    require(paper.is_array() && paper.size() == 2, "Invalid machine dimensions");
    for (int i=0;i<2;++i) require(paper[i].is_number() && std::isfinite(paper[i].get<double>()) && paper[i].get<double>() > 0 && paper[i].get<double>() <= 600, "Invalid machine dimensions");
    for (auto pair : std::map<int,double>{{130,paper[0].get<double>()},{131,paper[1].get<double>()},{132,10},{110,3000},{111,3000},{112,3000}})
        require(settings.count(pair.first) && settings[pair.first] >= pair.second, "Insufficient travel or speed");
    for (auto pair : std::map<int,double>{{3,7},{20,0},{21,0},{22,0},{100,80},{101,80},{102,100}})
        require(settings.count(pair.first) && settings[pair.first] == pair.second, "Unverified machine configuration");
    auto startup = command("$N");
    require(std::find(startup.begin(),startup.end(),"$N0=") != startup.end() && std::find(startup.begin(),startup.end(),"$N1=") != startup.end(), "Missing startup configuration");
    for (auto& line : startup) require(line == "$N0=" || line == "$N1=" || line == "ok", "Unverified startup command");
    return {{"status",report},{"settings",replies},{"modal",modal},{"parameters",parameters},{"startup",startup}};
}
void Controller::wait_idle(bool writing) {
    auto deadline = Clock::now() + (writing ? 90s : 35s);
    for (;;) {
        auto report = status();
        if (starts(report,"<Idle") && (!writing || !paused)) return;
        require(!starts(report,"<Alarm") && !starts(report,"<Door") && (writing || !starts(report,"<Hold")), "Motion did not finish normally");
        if (writing && paused) deadline = Clock::now() + 90s;
        require(Clock::now() < deadline, "Physical motion completion timeout"); std::this_thread::sleep_for(50ms);
    }
}
void Controller::join_finished() {
    if (busy) {
        auto snapshot=state();
        // Terminal motion state can be visible while the worker still saves
        // its final file and closes the log. Join that cleanup before allowing
        // the next operation; running/paused/draining work still rejects it.
        require(snapshot["phase"]=="complete" || snapshot["phase"]=="idle", "Another operation is active");
    }
    if (worker.joinable()) worker.join();
}
Json Controller::call(const std::string& action, const Json& data) {
    require(data.is_object(), "Expected JSON object");
    // These commands never wait for serial queries or uploads.
    if (action == "stop" || action == "pause" || action == "resume" || action == "manual-pause" || action == "manual-stop") {
        std::lock_guard<std::mutex> lock(tx_mutex); auto s = state();
        if (action == "stop" && s["phase"] == "stopping") return s;
        require(busy, "No operation is active");
        if (starts(action,"manual-")) {
            require(s["operation_type"] == "manual", "No manual operation is active"); manual_cancel = true;
            if (serial) { record("tx","\x85"); serial->write("\x85"); }
            update({{"phase","manual_stopping"},{"last_action","Manual movement cancelled"}});
        } else {
            require(s["operation_type"] == "writing", "No writing task is active");
            require(s["phase"] != "stopping" && s["phase"] != "checking", "Task cannot be controlled in this phase");
            if (action == "stop") {
                stopped = true; paused = false;
                update({{"phase","stopping"},{"motion_revision",s["motion_revision"].get<int>()+1}});
                try { record("tx","!\x18"); serial->write("!\x18"); } catch (const std::exception& e) { update({{"error",e.what()}}); }
            } else {
                require(action != "resume" || s["phase"] == "paused", "Task is not paused");
                paused = action == "pause";
                try { record("tx",paused ? "!" : "~"); serial->write(paused ? "!" : "~"); }
                catch (const std::exception& e) { stopped = true; paused = false; update({{"phase","stopping"},{"error",e.what()}}); throw; }
                update({{"phase",paused ? "paused" : "running"}});
            }
        }
        return state();
    }
    std::lock_guard<std::mutex> operation(api_mutex);
    try {
    if (action == "connect") {
        join_finished(); require(!serial && state()["connected"] == false, "Disconnect the current device first");
        auto port = data.at("port").get<std::string>(); bool allowed = false;
        auto available = ports();
        for (auto& entry : available["ports"]) if (entry["device"] == port) allowed = true;
        require(allowed, "Serial port is not in the allowed device list"); stopped = false; manual_cancel = false;
        serial = factory ? factory(port) : simulate ? simulated_serial() : open_serial(port);
        try {
            if (!simulate && !factory) { auto end = Clock::now()+2s; while (Clock::now()<end) read(); }
            require(starts(status(),"<Idle"), "Machine must be Idle");
            auto info = command("$I"); require(std::any_of(info.begin(),info.end(),[](const auto& s){return s.find("[VER:1.3a.20221114:") != std::string::npos;}), "Unverified firmware version");
            update({{"connected",true},{"phase","idle"},{"port",port},{"error",nullptr}});
        } catch (...) { serial.reset(); update({{"connected",false},{"position",nullptr},{"pen_top",nullptr},{"pen","unknown"}}); throw; }
        return state();
    }
    if (action == "disconnect") {
        join_finished(); require(state()["phase"] != "failed", "Handle the machine fault and restart the controller service"); serial.reset();
        update({{"connected",false},{"phase","disconnected"},{"port",""},{"status",""},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr}}); return state();
    }
    if (action == "start") {
        auto id = data.at("job_id").get<std::string>(); auto folder = job_path(id);
        std::lock_guard<std::mutex> jobs(jobs_mutex); auto meta = manifest(folder);
        if (fs::exists(folder/"consumed")) {
            auto s = state(); require(s["job_id"] == id, "Task already executed; generate a new task"); return s;
        }
        join_finished(); require(state()["connected"] == true, "Connect the machine first");
        require(state()["phase"] == "idle" || state()["phase"] == "complete", "Handle the current machine state first");
        require(data.value("origin_confirmed",false), "Confirm paper and origin first");
        require(fs::exists(folder/"ready"), "Task upload is incomplete"); validate_job(folder,meta);
        atomic_json(folder/"probe.json", check(meta));
        // A durable claim precedes any motion. Crashes require a new job ID.
        claim_job(folder);
        stopped = false; paused = false;
        update({{"phase","running"},{"operation_type","writing"},{"job_id",id},{"acknowledged",0},{"total",meta.at("commands")},{"error",nullptr}});
        try { save(folder); } catch (const std::exception& e) { failure(e.what(),false); throw; }
        busy = true;
        try { worker = std::thread(&Controller::run_job,this,folder); } catch (...) { busy=false; update({{"phase","failed"},{"operation_type",nullptr}}); throw; }
        return state();
    }
    join_finished(); require(state()["connected"] == true, "Connect the machine first");
    require(state()["phase"] == "idle" || state()["phase"] == "complete", "Handle the current machine state first");
    if (action == "check") { check(data.value("metadata",Json::object())); return state(); }
    std::vector<std::string> steps;
    if (action == "jog") {
        auto axis = data.at("axis").get<std::string>(); require(axis=="X" || axis=="Y", "Jog axis must be X or Y");
        double distance=numeric(data,"distance",-20,20), feed=numeric(data,"feed",50,1000); require(std::abs(distance)>=.01,"Jog distance must be at least 0.01 mm");
        std::ostringstream cmd; cmd << "$J=G21 G91 " << axis << distance << " F" << feed; steps.push_back(cmd.str());
    } else if (action == "pen") {
        auto position = data.at("position").get<std::string>(); require(position=="up" || position=="down","Invalid pen position");
        require(starts(status(),"<Idle"),"Machine must be Idle"); auto p=state()["position"]; require(p.is_array(),"Missing pen position");
        if (position=="up" && state()["pen_top"]==true) return state();
        if (position=="down") { if (p[2].get<double>()>.05) steps.push_back("$J=G21 G90 G53 Z0 F600"); steps.push_back("$J=G21 G90 G53 Z10 F600"); }
        steps.push_back("$J=G21 G90 G53 Z0 F600");
    } else throw std::runtime_error("Unknown control action");
    require(starts(status(),"<Idle"),"Machine must be Idle");
    manual_cancel=false; stopped=false; paused=false; auto s=state();
    update({{"phase","manual"},{"operation_type","manual"},{"motion_revision",s["motion_revision"].get<int>()+1},{"error",nullptr},{"last_action",action}});
    busy=true;
    try { worker=std::thread(&Controller::run_manual,this,steps); } catch (...) { busy=false; update({{"phase","failed"},{"operation_type",nullptr}}); throw; }
    return state();
    } catch (const SerialError& e) { failure(e.what(),false); throw; }
}
void Controller::failure(const std::string& message, bool manual) {
    try { if (serial) write(manual ? "\x85" : "!",true); } catch (...) {}
    update({{"phase","failed"},{"operation_type",nullptr},{"position",nullptr},{"pen_top",nullptr},{"pen","unknown"},{"error",message}});
}
void Controller::save(const fs::path& folder) { atomic_json(folder/"state.json",state()); }
void Controller::run_manual(std::vector<std::string> steps) {
    try {
        for (auto& step : steps) { if (manual_cancel) break; command(step,false,true); wait_idle(false); }
        std::lock_guard<std::mutex> tx(tx_mutex);
        update({{"phase","idle"},{"operation_type",nullptr},{"last_action",manual_cancel ? "Manual movement cancelled" : "Manual movement complete"}});
    } catch (const Cancelled&) {} catch (const std::exception& e) { failure(e.what(),true); }
    busy=false;
}
void Controller::run_job(fs::path folder) {
    try {
        { std::lock_guard<std::mutex> lock(log_mutex); log.open(folder/"serial.jsonl",std::ios::app); require(bool(log),"Cannot open serial log"); }
        std::ifstream file(folder/"job.gcode"); std::string line; size_t count=0;
        while (std::getline(file,line)) {
            command(line,true); cancelled(); update({{"acknowledged",++count}});
            if (count%25==0) save(folder);
        }
        require(file.eof(),"Cannot read task file");
        { std::lock_guard<std::mutex> tx(tx_mutex); cancelled(); if (!paused) update({{"phase","draining"}}); }
        wait_idle(true);
        auto parameters=command("$#"); std::vector<double> offset;
        for (auto& item : parameters) { std::smatch m; if (std::regex_match(item,m,std::regex("\\[G92:([0-9.,-]+)\\]"))) offset=values(m[1]); }
        auto p=state()["position"]; require(offset.size()==3 && p.is_array() && std::abs(offset[2])<=.01,"Missing final origin coordinates");
        for (size_t i=0;i<3;++i) require(std::abs(p[i].get<double>()-(i==2?0:offset[i]))<=.01,"Machine did not return to the task origin");
        { std::lock_guard<std::mutex> tx(tx_mutex); cancelled(); update({{"phase","complete"},{"operation_type",nullptr}}); }
    } catch (const Cancelled&) {} catch (const std::exception& e) { if (!stopped) failure(e.what(),false); }
    if (stopped) {
        std::lock_guard<std::mutex> tx(tx_mutex); serial.reset();
        update({{"phase","stopped"},{"connected",false},{"operation_type",nullptr},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr},{"status",""},{"last_action","Task stopped; reconnect and confirm origin"}});
    }
    try { save(folder); } catch (const std::exception& e) { failure(e.what(),false); }
    { std::lock_guard<std::mutex> lock(log_mutex); log.close(); }
    busy=false;
}

fs::path Controller::job_path(const std::string& id) { require(std::regex_match(id,std::regex("[a-f0-9]{32}")),"Invalid task ID"); return root/id; }
Json Controller::manifest(const fs::path& folder) { std::ifstream f(folder/"manifest.json"); require(bool(f),"Task not found"); Json j; f>>j; return j; }
void Controller::validate_job(const fs::path& folder, const Json& meta) {
    require(meta.contains("commands") && meta["commands"].is_number_integer(),"Invalid command count");
    auto total=meta["commands"].get<int>(); require(total>=7 && total<=1000000,"Invalid command count");
    require(meta.contains("sha256") && meta["sha256"].is_string() && std::regex_match(meta["sha256"].get<std::string>(),std::regex("[a-f0-9]{64}")),"Invalid SHA-256");
    auto paper=meta.at("machine_paper"), bounds=meta.at("machine_page_bounds");
    require(paper.is_array() && paper.size()==2 && bounds.is_array() && bounds.size()==4,"Missing machine bounds");
    for (auto& v : paper) require(v.is_number() && std::isfinite(v.get<double>()) && v.get<double>()>0 && v.get<double>()<=600,"Invalid machine dimensions");
    for (auto& v : bounds) require(v.is_number() && std::isfinite(v.get<double>()) && std::abs(v.get<double>())<=600,"Invalid machine bounds");
    for (int i=0;i<2;++i) require(bounds[i].get<double>()<=0 && bounds[i+2].get<double>()>=0 && std::abs(bounds[i+2].get<double>()-bounds[i].get<double>()-paper[i].get<double>())<.002,"Inconsistent machine bounds");
    auto path=folder/"job.gcode"; require(fs::file_size(path)<=32*1024*1024,"Task exceeds 32 MiB");
    std::ifstream raw(path,std::ios::binary); require(bool(raw),"Cannot read task"); picosha2::hash256_one_by_one hash; char chunk[8192];
    while (raw.read(chunk,sizeof(chunk)) || raw.gcount()) hash.process(chunk,chunk+raw.gcount());
    require(raw.eof(),"Cannot read task"); hash.finish(); require(picosha2::get_hash_hex_string(hash)==meta["sha256"],"Task checksum mismatch");
    std::ifstream f(path); std::string line, previous,last; int count=0;
    const std::vector<std::string> header={"G21","G90","G94","G1 Z0 F3000","G92 X0 Y0"};
    const std::regex motion("G1 (?:(X(-?[0-9]+(?:\\.[0-9]+)?) Y(-?[0-9]+(?:\\.[0-9]+)?))|Z(0|10)) F([0-9]+(?:\\.[0-9]+)?)");
    while (std::getline(f,line)) {
        require(line.size()<78,"Invalid G-code line");
        if (count<5) require(line==header[count],"Invalid task header");
        else {
            std::smatch m; require(std::regex_match(line,m,motion),"Unsupported G-code command");
            double feed=std::stod(m[5]); require(std::isfinite(feed) && feed>=100 && feed<=3000,"Invalid task feed");
            if (m[1].matched) for (int axis=0;axis<2;++axis) {
                double value=std::stod(m[axis+2]); require(value>=bounds[axis].get<double>()-.001 && value<=bounds[axis+2].get<double>()+.001,"Motion exceeds task bounds");
            }
        }
        previous=last; last=line; ++count;
    }
    require(count==total && previous=="G1 Z0 F3000" && last=="G1 X0 Y0 F3000","Invalid task trailer or line count");
}
Json Controller::upload(const std::string& id, const std::string& action, const Json& data) {
    require(data.is_object(),"Expected JSON object"); std::lock_guard<std::mutex> lock(jobs_mutex); auto folder=job_path(id);
    if (action=="begin") {
        if (fs::exists(folder/"manifest.json")) require(manifest(folder)==data,"Task ID already contains different content");
        else { fs::create_directories(folder); atomic_json(folder/"manifest.json",data); std::ofstream f(folder/"job.gcode",std::ios::binary); require(bool(f),"Cannot create task file"); }
    } else {
        manifest(folder); require(!fs::exists(folder/"consumed"),"Task already executed");
        if (action=="chunk") {
            require(!fs::exists(folder/"ready"),"Task is already sealed");
            require(data.at("offset").is_number_unsigned() || data.at("offset").is_number_integer(),"Invalid upload offset");
            auto offset=data.at("offset").get<int64_t>(); auto content=data.at("content").get<std::string>();
            require(offset>=0 && content.size()<=32768 && offset+content.size()<=32*1024*1024,"Invalid upload size");
            for (unsigned char c : content) require(c=='\n' || (c>=32 && c<127),"Task must contain ASCII G-code");
            auto size=fs::file_size(folder/"job.gcode");
            if (static_cast<uint64_t>(offset)<size) {
                require(offset+content.size()<=size,"Overlapping task chunk");
                std::ifstream f(folder/"job.gcode",std::ios::binary); f.seekg(offset); std::string existing(content.size(),'\0'); f.read(existing.data(),existing.size());
                require(existing==content,"Conflicting task chunk");
            } else {
                require(static_cast<uint64_t>(offset)==size,"Unexpected upload offset");
                std::ofstream f(folder/"job.gcode",std::ios::binary|std::ios::app); f<<content; f.flush(); require(bool(f),"Cannot write task chunk");
            }
        } else if (action=="seal") {
            validate_job(folder,manifest(folder)); std::ofstream ready(folder/"ready"); ready<<"validated\n"; ready.flush(); require(bool(ready),"Cannot seal task");
        } else throw std::runtime_error("Unknown upload action");
    }
    return {{"job_id",id},{"offset",fs::file_size(folder/"job.gcode")},{"ready",fs::exists(folder/"ready")},{"consumed",fs::exists(folder/"consumed")}};
}
}
